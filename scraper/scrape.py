#!/usr/bin/env python3
"""
Ringside Scraper — pulls upcoming ONE Championship fight cards
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


def detect_results(soup: BeautifulSoup, fights: list[dict], event_past: bool):
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

    # Fighter pairs — find all athlete profile links
    athlete_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/")
        # Skip the generic "/athletes" directory link
        if href.endswith("/athletes") or href == "/athletes":
            continue
        if "/athletes/" in a["href"]:
            name = a.get_text(strip=True)
            # Skip empty, single-char, or generic nav text
            if name and len(name) > 1 and name.lower() not in ("all athletes", "athletes", "view all"):
                athlete_links.append({"name": name, "href": a["href"]})

    # Deduplicate preserving order
    seen_names = []
    unique = []
    for al in athlete_links:
        if al["name"] not in seen_names:
            seen_names.append(al["name"])
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
            },
            "fighter_b": {
                "name": fb["name"],
                "country": "",
                "flag": "",
                "profile": urljoin(BASE, fb["href"]),
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
    detect_results(soup, fights, past)

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
                text = soup.get_text()
                # Record in format like "13-0-0 (Win-Loss-Draw)"
                m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,3})", text)
                if m:
                    fighter["record"] = m.group(0)
                else:
                    fighter["record"] = ""

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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data", help="Output directory for events.json")
    parser.add_argument("--enrich", action="store_true",
                        help="Fetch fighter profiles for win/loss records (slower)")
    parser.add_argument("--max", type=int, default=4, help="Max events to scrape")
    args = parser.parse_args()

    print("=== Ringside Scraper ===")
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
