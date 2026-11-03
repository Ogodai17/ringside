#!/usr/bin/env python3
"""
Pick A Fight Scraper — pulls upcoming ONE Championship fight cards
from onefc.com and outputs events.json for the frontend.

Run locally:
    pip install requests beautifulsoup4
    python scraper/scrape.py

Or via GitHub Actions (see .github/workflows/scrape.yml).
"""

import json
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE = "https://www.onefc.com"
EVENTS_URL = f"{BASE}/events/"

# Country code to flag emoji mapping
COUNTRY_FLAGS = {
    "Turkey": "🇹🇷", "United Kingdom": "🇬🇧", "England": "🇬🇧",
    "United States": "🇺🇸", "Thailand": "🇹🇭", "Iran": "🇮🇷",
    "Brazil": "🇧🇷", "South Korea": "🇰🇷", "Armenia": "🇦🇲",
    "Japan": "🇯🇵", "Malaysia": "🇲🇾", "Russia": "🇷🇺",
    "China": "🇨🇳", "France": "🇫🇷", "Philippines": "🇵🇭",
    "Myanmar": "🇲🇲", "Indonesia": "🇮🇩", "New Zealand": "🇳🇿",
    "Australia": "🇦🇺", "Canada": "🇨🇦", "India": "🇮🇳",
    "Azerbaijan": "🇦🇿", "Kyrgyzstan": "🇰🇬", "Kazakhstan": "🇰🇿",
    "Uzbekistan": "🇺🇿", "Mexico": "🇲🇽", "Netherlands": "🇳🇱",
    "Belgium": "🇧🇪", "Singapore": "🇸🇬", "Cambodia": "🇰🇭",
    "Vietnam": "🇻🇳", "Morocco": "🇲🇦", "Mongolia": "🇲🇳",
}


def fetch(url: str, retries: int = 2) -> BeautifulSoup | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if attempt < retries:
                print(f"  Retry {attempt+1} for {url}", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"  [!] Failed: {url} — {e}", file=sys.stderr)
                return None


def discover_events(soup: BeautifulSoup) -> list[dict]:
    """Find upcoming event URLs from the events listing page."""
    seen = set()
    events = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/events/" not in href or href in ("/events/", "/events/#upcoming", "/events/#past"):
            continue
        if any(x in href for x in ["#", "category", "javascript"]):
            continue
        full = urljoin(BASE, href).rstrip("/") + "/"
        slug = full.rstrip("/").split("/")[-1]
        if slug and slug not in seen and full.startswith(BASE + "/events/"):
            seen.add(slug)
            events.append({"url": full, "slug": slug})
    return events


def classify_discipline(label: str) -> str:
    label_lower = label.lower()
    if "mma" in label_lower:
        return "mma"
    if "muay thai" in label_lower:
        return "mt"
    if "kickboxing" in label_lower:
        return "kb"
    if "grappling" in label_lower:
        return "grappling"
    return "unknown"


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _valid_date(y: int, mo: int, d: int) -> str:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ""


def detect_event_date(soup: BeautifulSoup, today: date | None = None) -> str:
    """Extract the event date as YYYY-MM-DD.

    NOTE: never use meta published/modified times — those are when the
    article was written/edited, not when the fights happen (this was the
    old bug that dated Oct 30 events as Sep 1).
    """
    today = today or date.today()

    # 1) onefc.com names event artwork like "261030-BKK-OFF176_TIC36-1800x1200px.jpg"
    #    (YYMMDD prefix). Take the most common code across og:image + <img> tags.
    codes = []
    for tag in soup.find_all(["meta", "img", "source"]):
        for attr in ("content", "src", "srcset", "data-src"):
            val = tag.get(attr) or ""
            for m in re.finditer(r"/(\d{2})(\d{2})(\d{2})-[A-Za-z]", val):
                iso = _valid_date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if iso:
                    codes.append(iso)
    if codes:
        return Counter(codes).most_common(1)[0][0]

    # 2) Text like "October 30, 2026" / "30 October 2026"
    text = soup.get_text(" ", strip=True)
    months_re = "|".join(MONTHS)
    m = re.search(rf"\b({months_re})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", text)
    if m:
        return _valid_date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2)))
    m = re.search(rf"\b(\d{{1,2}})\s+({months_re})\s+(\d{{4}})\b", text)
    if m:
        return _valid_date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1)))

    # 3) Text like "on October 30" with no year — pick the year that puts
    #    the date closest to today.
    m = re.search(rf"\bon\s+({months_re})\s+(\d{{1,2}})\b", text)
    if m:
        mo, d = MONTHS.index(m.group(1)) + 1, int(m.group(2))
        candidates = [_valid_date(y, mo, d) for y in (today.year - 1, today.year, today.year + 1)]
        candidates = [c for c in candidates if c]
        if candidates:
            return min(candidates, key=lambda c: abs((date.fromisoformat(c) - today).days))
    return ""


def event_is_past(event_date: str, today: date | None = None) -> bool:
    """True once the day after the event has started (results can exist)."""
    if not event_date:
        return False
    today = today or date.today()
    try:
        return date.fromisoformat(event_date) < today
    except ValueError:
        return False


RESULT_MARK = re.compile(r"^\s*(WIN|WINNER|DRAW|NO\s*CONTEST|NC)(?![A-Za-z])")
# Same marker with the method glued on, as get_text(strip=True) shows it:
# "WINKnockout (R2)KO (R2)", "DRAWMajority Draw (R3)"
RESULT_MARK_JOINED = re.compile(
    r"^(WIN|DRAW|NO CONTEST|NC)(?=(Knockout|Technical|TKO|KO|Submission|Unanimous|Split|"
    r"Majority|Decision|Disqualification|DQ|Draw|No Contest)\b)")


def result_from_marks(fa: dict, fb: dict, marks: dict) -> dict | None:
    """Read a bout result from onefc.com's WIN/DRAW profile-link markers."""
    ma, mb = marks.get(fa.get("_slug", "")), marks.get(fb.get("_slug", ""))
    if not ma and not mb:
        return None
    m = ma or mb
    outcome = m["outcome"].replace(" ", "")
    txt = m["text"].lower()
    rm = re.search(r"\(\s*r(?:ound)?\s*([1-5])\s*\)|\bround\s*([1-5])\b", txt)
    rnd = int(rm.group(1) or rm.group(2)) if rm else None
    if outcome in ("DRAW", "NOCONTEST", "NC"):
        return {"winner": "draw", "method": "Draw" if outcome == "DRAW" else "No Contest", "round": rnd}
    if ma and mb:
        return None  # both marked as winner — don't guess
    if re.search(r"\b(t?ko|knockout)\b", txt):
        method = "KO/TKO"
    elif re.search(r"\b(submission|sub)\b", txt):
        method = "Submission"
    elif re.search(r"\b(decision|ud|sd|md)\b", txt):
        method = "Decision"
    else:
        method = None
    return {"winner": "a" if ma else "b", "method": method, "round": rnd}


def detect_results(soup: BeautifulSoup, fights: list[dict], event_past: bool, marks: dict | None = None):
    """Annotate fights with winner/method — only for events that already happened.

    Old version matched words like "Results" in the site menu and the
    "Winner" inside fighter names (e.g. "Superbest Winnermuaythai"), which
    marked every upcoming fight as a KO win for fighter A. Now:
      - upcoming events never get results
      - a WIN/WINNER label must be its own standalone text, not a substring
      - method/round use whole-word matches
    A past fight with no clear result stays None (shown as "awaiting result").
    """
    for f in fights:
        f["result"] = None
    if not event_past:
        return

    if marks:
        for f in fights:
            f["result"] = result_from_marks(f["fighter_a"], f["fighter_b"], marks)
        return

    win_label = re.compile(r"^\s*(WIN|WINNER|WINS)\s*$", re.I)
    labels = soup.find_all(string=win_label)

    all_names = [(f["fighter_a"]["name"].lower(), f["fighter_b"]["name"].lower()) for f in fights]

    for i, f in enumerate(fights):
        name_a, name_b = all_names[i]
        other_names = [n for j, pair in enumerate(all_names) if j != i for n in pair]
        for tag in labels:
            # Walk up to the smallest block holding BOTH fighters of this
            # bout and NO fighters from other bouts.
            block, txt = tag.parent, ""
            for _ in range(5):
                if block is None:
                    break
                txt = block.get_text(" ", strip=True).lower()
                if any(n in txt for n in other_names):
                    block = None
                    break
                if name_a in txt and name_b in txt:
                    break
                block = block.parent
            if block is None or not (name_a in txt and name_b in txt):
                continue

            # Winner = fighter name nearest to the WIN label (edge to edge),
            # measured in the label's own position within this block.
            before = []
            for s in block.strings:
                if s is tag:
                    break
                before.append(s.strip())
            label_pos = len(" ".join(x for x in before if x).lower()) + 1
            def dist(name):
                p = txt.find(name)
                return min(abs(p - label_pos), abs(p + len(name) - label_pos))
            winner = "a" if dist(name_a) <= dist(name_b) else "b"

            method = "Decision"
            if re.search(r"\b(t?ko|knockout)\b", txt):
                method = "KO/TKO"
            elif re.search(r"\b(submission|sub)\b", txt):
                method = "Submission"
            elif re.search(r"\bdraw\b", txt):
                method, winner = "Draw", "draw"

            round_num = None
            rm = re.search(r"\b(?:round|rd\.?|r)\s*([1-5])\b", txt)
            if rm:
                round_num = int(rm.group(1))

            f["result"] = {"winner": winner, "method": method, "round": round_num}
            break


def parse_event(soup: BeautifulSoup, url: str) -> dict | None:
    """Parse one event page into structured JSON."""

    # Title
    title_el = soup.find("title")
    raw_title = title_el.text.strip() if title_el else ""
    event_name = raw_title.split(" - ")[0].split(" on ")[0].strip()
    subtitle = ""
    if ":" in event_name:
        event_name, subtitle = event_name.split(":", 1)
        event_name = event_name.strip()
        subtitle = subtitle.strip()

    # Venue — grab first meaningful location text
    venue = ""
    for s in soup.stripped_strings:
        if any(kw in s for kw in ["Stadium", "Arena", "Buntai", "Coliseum", "Dome"]):
            if len(s) < 80:
                venue = s.strip()
                break

    # Discipline labels — collect them in order
    disc_labels = []
    for s in soup.stripped_strings:
        s = s.strip()
        if re.match(r"^(Atom|Straw|Fly|Bantam|Feather|Light|Welter|Middle|Heavy|Women)", s):
            if any(d in s for d in ["MMA", "Muay Thai", "Kickboxing", "Grappling"]):
                disc_labels.append(s)

    # Fighter pairs — find all athlete profile links.
    # After an event, onefc.com adds a second link to the WINNER's profile
    # whose text is the result, e.g. "WIN" + "Knockout (R2)" + "KO (R2)".
    # Those are result markers, not fighters — treating them as names
    # shifted every pairing on the first real results page (FF176).
    athlete_links = []
    result_marks = {}  # profile slug -> {"outcome", "text"}
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/")
        # Skip the generic "/athletes" directory link
        if href.endswith("/athletes") or href == "/athletes":
            continue
        if "/athletes/" in a["href"]:
            slug = href.split("/")[-1].lower()
            name = a.get_text(strip=True)
            mark = RESULT_MARK.match(a.get_text(" ", strip=True)) or RESULT_MARK_JOINED.match(name)
            if mark:
                result_marks.setdefault(slug, {"outcome": mark.group(1).upper(),
                                               "text": a.get_text(" ", strip=True)})
                continue
            # Skip empty, single-char, or generic nav text
            if name and len(name) > 1 and name.lower() not in ("all athletes", "athletes", "view all"):
                athlete_links.append({"name": name, "href": a["href"], "slug": slug})

    # Deduplicate by profile (same athlete linked from photo + name)
    seen = set()
    unique = []
    for al in athlete_links:
        if al["slug"] not in seen:
            seen.add(al["slug"])
            unique.append(al)

    # Pair fighters into bouts
    # (Country/flag enrichment happens in enrich_fighters via profile pages)
    fights = []
    for i in range(0, len(unique) - 1, 2):
        fa = unique[i]
        fb = unique[i + 1]
        fight_idx = i // 2

        label = disc_labels[fight_idx] if fight_idx < len(disc_labels) else f"Bout {fight_idx+1}"
        disc = classify_discipline(label)

        fights.append({
            "id": fight_idx + 1,
            "label": label,
            "discipline": disc,
            "fighter_a": {
                "name": fa["name"],
                "country": "",
                "flag": "",
                "profile": urljoin(BASE, fa["href"]),
                "_slug": fa["slug"],
            },
            "fighter_b": {
                "name": fb["name"],
                "country": "",
                "flag": "",
                "profile": urljoin(BASE, fb["href"]),
                "_slug": fb["slug"],
            },
        })

    if not fights:
        return None

    # Mark main event
    if fights:
        fights[0]["main_event"] = True

    # Date first — status and results depend on it
    event_date = detect_event_date(soup)
    past = event_is_past(event_date)
    detect_results(soup, fights, past, result_marks)
    for f in fights:
        f["fighter_a"].pop("_slug", None)
        f["fighter_b"].pop("_slug", None)

    return {
        "name": event_name,
        "subtitle": subtitle,
        "date": event_date,
        "venue": venue,
        "url": url,
        "status": "completed" if past else "upcoming",
        "fight_count": len(fights),
        "fights": fights,
    }


def extract_country(soup: BeautifulSoup) -> str:
    """Try to pull country from a fighter profile page."""
    # Common patterns: country appears in meta tags, structured data, or text near flag/nationality
    # Try og:description or meta description first
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        for country in COUNTRY_FLAGS:
            if country in content:
                return country

    # Try finding country names in text near common labels
    text = soup.get_text(" ", strip=True)
    for label in ["Nationality", "Country", "Born", "Fighting out of", "Hometown"]:
        idx = text.find(label)
        if idx != -1:
            snippet = text[idx:idx+80]
            for country in COUNTRY_FLAGS:
                if country in snippet:
                    return country

    # Fallback: scan full text for any known country name
    # (check longer names first to avoid "New" matching before "New Zealand")
    sorted_countries = sorted(COUNTRY_FLAGS.keys(), key=len, reverse=True)
    for country in sorted_countries:
        if country in text:
            return country

    return ""


def parse_record(text: str) -> str:
    """Fighter record as "W-L-D" from a ONE profile page.

    onefc.com shows it as separate labels ("Wins - 11", "Losses - 5"),
    not a single string. The old "\\d-\\d-\\d" search found nothing on most
    pages and could match part of a date ("2026-10-30" -> "026-10-30").
    """
    w = re.search(r"\bWins\b\W{0,6}(\d{1,3})\b", text)
    l = re.search(r"\bLosses\b\W{0,6}(\d{1,3})\b", text)
    if w and l:
        d = re.search(r"\bDraws\b\W{0,6}(\d{1,3})\b", text)
        return f"{w.group(1)}-{l.group(1)}-{d.group(1) if d else 0}"
    # Fallback: an explicit "13-0-0 (W-L-D)" style string, never a bare date
    m = re.search(r"(?<![\d-])(\d{1,3})-(\d{1,3})-(\d{1,3})(?![\d-])\s*\(?\s*(?:W|Win)", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def enrich_fighters(event: dict, delay: float = 0.5):
    """Fetch each fighter's profile page to get their record and country."""
    for fight in event["fights"]:
        for side in ("fighter_a", "fighter_b"):
            fighter = fight[side]
            profile_url = fighter.get("profile", "")
            if not profile_url:
                continue

            print(f"    Enrich: {fighter['name']}...", end=" ", flush=True)
            soup = fetch(profile_url)
            if soup:
                text = soup.get_text(" ", strip=True)
                fighter["record"] = parse_record(text)
                fr = re.search(r"Finish Rate\W{0,10}(\d{1,3})\s*%", text)
                if fr:
                    fighter["finish_rate"] = int(fr.group(1))

                # Country + flag
                if not fighter.get("country"):
                    country = extract_country(soup)
                    fighter["country"] = country
                    fighter["flag"] = COUNTRY_FLAGS.get(country, "")

                status = fighter["record"] or "no record"
                if fighter["country"]:
                    status += f", {fighter['country']}"
                print(status)
            else:
                fighter["record"] = fighter.get("record", "")
                print("failed")

            time.sleep(delay)


RECHECK_DAYS = 4         # re-read a finished event's page for results this long
KEEP_FINISHED_DAYS = 14  # keep it on the site this long so late visitors' picks settle
CANCELLED = {"winner": "void", "method": "Cancelled", "round": None}


def _slug_of(fighter: dict) -> str:
    return (fighter.get("profile") or "").rstrip("/").split("/")[-1].lower()


def _fighter_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def merge_previous(new_ev: dict, old_ev: dict) -> tuple[dict, list[dict]]:
    """Carry over fighter details (country, record...) and any result we
    already had, so a re-check never loses data or re-fetches profiles.
    Also returns the saved bouts the new page no longer shows."""
    # Picks are keyed on event name + fighter names (A then B), so keep the
    # saved name and each bout's A/B order exactly as players saw them.
    for k in ("name", "subtitle", "venue"):
        if old_ev.get(k):
            new_ev[k] = old_ev[k]
    old_by_pair, old_by_slug = {}, {}
    for f in old_ev.get("fights", []):
        key = (_fighter_key(f["fighter_a"]["name"]), _fighter_key(f["fighter_b"]["name"]))
        old_by_pair[key] = f
        slugs = (_slug_of(f["fighter_a"]), _slug_of(f["fighter_b"]))
        if all(slugs):
            old_by_slug[slugs] = f
    matched = set()
    for f in new_ev.get("fights", []):
        a, b = _fighter_key(f["fighter_a"]["name"]), _fighter_key(f["fighter_b"]["name"])
        sa, sb = _slug_of(f["fighter_a"]), _slug_of(f["fighter_b"])
        # Same bout by name, or by profile link if a name's spelling changed
        old = old_by_pair.get((a, b)) or old_by_slug.get((sa, sb))
        if old is None and ((b, a) in old_by_pair or (sb, sa) in old_by_slug):
            # Results page lists this bout the other way round — flip it back.
            old = old_by_pair.get((b, a)) or old_by_slug[(sb, sa)]
            f["fighter_a"], f["fighter_b"] = f["fighter_b"], f["fighter_a"]
            r = f.get("result")
            if r and r.get("winner") in ("a", "b"):
                r["winner"] = "b" if r["winner"] == "a" else "a"
        if old is None:
            continue
        matched.add(id(old))
        f["fighter_a"]["name"], f["fighter_b"]["name"] = old["fighter_a"]["name"], old["fighter_b"]["name"]
        for side in ("fighter_a", "fighter_b"):
            for k, v in old[side].items():
                if k not in f[side] or not f[side][k]:
                    f[side][k] = v
        for k in ("odds", "id", "label", "discipline", "main_event"):
            if k in old and k not in f:
                f[k] = old[k]
        old_r = old.get("result")
        # A cancelled bout stays cancelled: its picks were already refunded
        if old_r and (old_r.get("winner") == "void" or not f.get("result")):
            f["result"] = old_r
    # Bouts we had but the results page doesn't show: keep them so picks
    # on them aren't orphaned.
    missing = [f for f in old_ev.get("fights", []) if id(f) not in matched]
    new_ev["fights"].extend(missing)
    return new_ev, missing


def mark_cancelled(missing: list[dict], saved_total: int, page_total: int, page_with_result: int) -> int:
    """A bout that's gone from a finished event's results page didn't happen.
    Only trust that when the page clearly shows results (at least half of
    its bouts have one) AND it matched most of the saved card (at most 2
    bouts or a quarter missing) — so a half-loaded or mis-read page can
    never cancel a whole card."""
    if not page_total or page_with_result * 2 < page_total:
        return 0
    if not missing or len(missing) >= saved_total or len(missing) > max(2, saved_total // 4):
        if missing:
            print(f"::warning title=ONE bouts not cancelled::{len(missing)} of {saved_total} saved bouts "
                  f"weren't found on the results page — too many to trust; left open for a human to check.")
        return 0
    n = 0
    for f in missing:
        if not f.get("result"):
            f["result"] = dict(CANCELLED)
            n += 1
    return n


def recheck_recent_events(out_file: Path, scraped_urls: set, today: date | None = None) -> list[dict]:
    """Re-scrape events from the previous run that are no longer on the
    upcoming list: re-read pages for RECHECK_DAYS, then keep the saved card
    until KEEP_FINISHED_DAYS so late visitors' picks still settle."""
    today = today or date.today()
    try:
        previous = json.loads(out_file.read_text(encoding="utf-8")).get("events", [])
    except (OSError, ValueError):
        return []

    kept = []
    for old in previous:
        url, ev_date = old.get("url"), old.get("date") or ""
        if not url or url in scraped_urls:
            continue
        try:
            age = (today - date.fromisoformat(ev_date)).days
        except ValueError:
            continue
        if age < 0 or age > KEEP_FINISHED_DAYS:
            continue  # still upcoming but unlisted (postponed?) or too old
        if age > RECHECK_DAYS:
            kept.append(old)  # results are settled by now; keep as saved
            continue

        print(f"\n  [re-check finished] {old.get('name')} ({ev_date})")
        soup = fetch(url)
        parsed = parse_event(soup, url) if soup else None
        names = lambda e: {_fighter_key(f[s]["name"]) for f in (e or {}).get("fights", [])
                           for s in ("fighter_a", "fighter_b")}
        if parsed and names(old) & names(parsed):
            # Count before merging — merging copies in results we already had
            page_total = len(parsed["fights"])
            page_with_result = sum(1 for f in parsed["fights"] if f.get("result"))
            ev, missing = merge_previous(parsed, old)
            ev["date"] = ev_date or ev.get("date")
            if age > 0:
                n_cx = mark_cancelled(missing, len(old.get("fights", [])), page_total, page_with_result)
                if n_cx:
                    print(f"::notice title=ONE bouts cancelled::{ev.get('name')}: {n_cx} bout(s) not on the "
                          f"results page — marked cancelled, picks refunded")
        else:
            # Page failed or looks totally different — keep what we had.
            print("  -> couldn't re-read the page, keeping saved card", file=sys.stderr)
            ev = old
        if age > 0:
            ev["status"] = "completed"
        ev["fight_count"] = len(ev.get("fights", []))
        n_res = sum(1 for f in ev.get("fights", []) if f.get("result"))
        print(f"  -> {n_res}/{ev['fight_count']} results")
        if age > 0 and n_res == 0:
            print(f"::warning title=ONE results missing::{ev.get('name')} finished but no results "
                  f"were found on {url} — the results reader may need updating.")
        else:
            print(f"::notice title=ONE results::{ev.get('name')}: {n_res}/{ev['fight_count']} results")
        kept.append(ev)
    return kept


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data", help="Output directory for events.json")
    parser.add_argument("--enrich", action="store_true",
                        help="Fetch fighter profiles for win/loss records (slower)")
    parser.add_argument("--max", type=int, default=4, help="Max events to scrape")
    args = parser.parse_args()

    print("=== Pick A Fight Scraper ===")
    print(f"Fetching {EVENTS_URL}...")

    listing = fetch(EVENTS_URL)
    if not listing:
        print("Could not load events page.", file=sys.stderr)
        sys.exit(1)

    event_links = discover_events(listing)
    print(f"Found {len(event_links)} event links, scraping up to {args.max}")

    results = []
    for ev in event_links[:args.max]:
        print(f"\n  [{ev['slug']}]")
        soup = fetch(ev["url"])
        if not soup:
            continue

        parsed = parse_event(soup, ev["url"])
        if parsed:
            print(f"  -> {parsed['fight_count']} fights")

            if args.enrich:
                enrich_fighters(parsed)

            results.append(parsed)
        else:
            print("  -> no fights found, skipping")

    # Finished events drop off onefc.com's upcoming list, so without this
    # they'd vanish the day after (before results are read) and picks on
    # them would never settle. Re-check them by their saved URL.
    out_file = Path(args.out) / "events.json"
    results += recheck_recent_events(out_file, {e["url"] for e in results})

    # Safety net: never overwrite good data with an empty scrape.
    # Exit non-zero so the GitHub Actions run shows a red X.
    if not results:
        print("\n[!] Scraped 0 events — keeping previous data/events.json. "
              "The site layout may have changed.", file=sys.stderr)
        sys.exit(1)

    # Write output
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "events.json"

    output = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "onefc.com",
        "event_count": len(results),
        "events": results,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Wrote {len(results)} events to {out_file}")


if __name__ == "__main__":
    main()
