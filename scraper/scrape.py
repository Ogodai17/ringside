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


def detect_event_date(soup: BeautifulSoup) -> str:
    """Try to extract event date from the page."""
    # Look for date patterns in meta tags
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        # ISO date format
        m = re.search(r"(\d{4}-\d{2}-\d{2})", content)
        if m:
            return m.group(1)
    # Look for date in page text (e.g. "October 25, 2026" or "25 October 2026")
    text = soup.get_text()
    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    m = re.search(rf"({months})\s+(\d{{1,2}}),?\s+(\d{{4}})", text)
    if m:
        return f"{m.group(1)} {m.group(2)}, {m.group(3)}"
    m = re.search(rf"(\d{{1,2}})\s+({months})\s+(\d{{4}})", text)
    if m:
        return f"{m.group(2)} {m.group(1)}, {m.group(3)}"
    return ""


def detect_results(soup: BeautifulSoup, fights: list[dict]):
    """Check if event has results and annotate fights with winner/method."""
    page_text = soup.get_text(" ", strip=True).lower()
    has_results = any(w in page_text for w in ["result", "winner", " win ", " won ", "defeated"])

    if not has_results:
        for f in fights:
            f["result"] = None  # upcoming / no result yet
        return

    # Try to find result blocks near each fight's fighters
    for f in fights:
        f["result"] = None  # default
        name_a = f["fighter_a"]["name"]
        name_b = f["fighter_b"]["name"]

        # Search for result indicators near fighter names
        # Common patterns: "Fighter Name WIN", "Fighter Name defeated", result method text
        for tag in soup.find_all(string=re.compile(r"(WIN|WINNER|Defeated|Victory)", re.I)):
            context = tag.parent.get_text(" ", strip=True) if tag.parent else str(tag)
            parent_block = tag.parent.parent.get_text(" ", strip=True) if tag.parent and tag.parent.parent else context

            winner = None
            if name_a.lower() in parent_block.lower():
                winner = "a"
            elif name_b.lower() in parent_block.lower():
                winner = "b"

            if winner:
                # Try to detect method
                method = "Decision"  # default
                block_lower = parent_block.lower()
                if any(k in block_lower for k in ["ko", "tko", "knockout"]):
                    method = "KO/TKO"
                elif any(k in block_lower for k in ["sub", "submission", "tap"]):
                    method = "Submission"
                elif "draw" in block_lower:
                    method = "Draw"
                    winner = "draw"

                # Detect round
                round_num = None
                rm = re.search(r"(?:round|r)\s*(\d)", block_lower)
                if rm:
                    round_num = int(rm.group(1))

                f["result"] = {
                    "winner": winner,
                    "method": method,
                    "round": round_num,
                }
                break  # found result for this fight


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

    # Detect results for completed events
    detect_results(soup, fights)
    has_results = any(f.get("result") for f in fights)

    # Try to get event date
    event_date = detect_event_date(soup)

    return {
        "name": event_name,
        "subtitle": subtitle,
        "date": event_date,
        "venue": venue,
        "url": url,
        "status": "completed" if has_results else "upcoming",
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
