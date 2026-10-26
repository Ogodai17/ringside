#!/usr/bin/env python3
"""
Ringside UFC Scraper — pulls upcoming and recent UFC fight cards
from ufcstats.com and outputs ufc_events.json for the frontend.

Uses the same output format as scrape.py (ONE Championship) so the
frontend can load both seamlessly.

Run locally:
    pip install requests beautifulsoup4
    python scraper/scrape_ufc.py

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

BASE = "http://ufcstats.com"
UPCOMING_URL = f"{BASE}/statistics/events/upcoming?page=all"
COMPLETED_URL = f"{BASE}/statistics/events/completed?page=all"

# Country code to flag emoji mapping (expanded for UFC's global roster)
COUNTRY_FLAGS = {
    "United States": "🇺🇸", "USA": "🇺🇸",
    "Brazil": "🇧🇷",
    "Russia": "🇷🇺",
    "United Kingdom": "🇬🇧", "England": "🇬🇧", "Scotland": "🇬🇧", "Wales": "🇬🇧",
    "Ireland": "🇮🇪",
    "Canada": "🇨🇦",
    "Australia": "🇦🇺",
    "New Zealand": "🇳🇿",
    "Mexico": "🇲🇽",
    "Japan": "🇯🇵",
    "South Korea": "🇰🇷",
    "China": "🇨🇳",
    "Thailand": "🇹🇭",
    "France": "🇫🇷",
    "Germany": "🇩🇪",
    "Netherlands": "🇳🇱",
    "Sweden": "🇸🇪",
    "Poland": "🇵🇱",
    "Nigeria": "🇳🇬",
    "Cameroon": "🇨🇲",
    "South Africa": "🇿🇦",
    "Jamaica": "🇯🇲",
    "Argentina": "🇦🇷",
    "Colombia": "🇨🇴",
    "Peru": "🇵🇪",
    "Ecuador": "🇪🇨",
    "Chile": "🇨🇱",
    "Cuba": "🇨🇺",
    "Dominican Republic": "🇩🇴",
    "Georgia": "🇬🇪",
    "Armenia": "🇦🇲",
    "Azerbaijan": "🇦🇿",
    "Kazakhstan": "🇰🇿",
    "Uzbekistan": "🇺🇿",
    "Kyrgyzstan": "🇰🇬",
    "Tajikistan": "🇹🇯",
    "Dagestan": "🇷🇺",
    "Czech Republic": "🇨🇿", "Czechia": "🇨🇿",
    "Italy": "🇮🇹",
    "Spain": "🇪🇸",
    "Portugal": "🇵🇹",
    "Denmark": "🇩🇰",
    "Norway": "🇳🇴",
    "Finland": "🇫🇮",
    "Switzerland": "🇨🇭",
    "Belgium": "🇧🇪",
    "Austria": "🇦🇹",
    "Romania": "🇷🇴",
    "Serbia": "🇷🇸",
    "Croatia": "🇭🇷",
    "Moldova": "🇲🇩",
    "Ukraine": "🇺🇦",
    "Belarus": "🇧🇾",
    "Lithuania": "🇱🇹",
    "Latvia": "🇱🇻",
    "Philippines": "🇵🇭",
    "Indonesia": "🇮🇩",
    "India": "🇮🇳",
    "Iran": "🇮🇷",
    "Turkey": "🇹🇷",
    "Morocco": "🇲🇦",
    "Tunisia": "🇹🇳",
    "Algeria": "🇩🇿",
    "Egypt": "🇪🇬",
    "Israel": "🇮🇱",
    "Mongolia": "🇲🇳",
    "Singapore": "🇸🇬",
    "Malaysia": "🇲🇾",
    "Myanmar": "🇲🇲",
    "Cambodia": "🇰🇭",
    "Vietnam": "🇻🇳",
    "Guyana": "🇬🇾",
    "Trinidad and Tobago": "🇹🇹",
    "Puerto Rico": "🇵🇷",
    "Guam": "🇬🇺",
    "Hawaii": "🇺🇸",
}


def fetch(url: str, retries: int = 2) -> BeautifulSoup | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            if "checking your browser" in r.text[:5000].lower():
                # ufcstats.com now sometimes serves a JavaScript bot check
                # instead of the real page; plain requests can't pass it.
                print(f"  [!] Bot check page (needs JavaScript): {url}", file=sys.stderr)
                return None
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            if attempt < retries:
                print(f"  Retry {attempt+1} for {url}", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"  [!] Failed: {url} — {e}", file=sys.stderr)
                return None


def discover_events(upcoming: bool = True, max_events: int = 6) -> list[dict]:
    """Find event URLs from the ufcstats event listing pages."""
    url = UPCOMING_URL if upcoming else COMPLETED_URL
    label = "upcoming" if upcoming else "completed"
    print(f"  Fetching {label} events from {url}...")

    soup = fetch(url)
    if not soup:
        return []

    events = []
    # ufcstats uses <a> tags with href containing "event-details"
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "event-details" not in href:
            continue

        name = a.get_text(strip=True)
        if not name or len(name) < 3:
            continue

        # Deduplicate (same link appears multiple times)
        full_url = href if href.startswith("http") else urljoin(BASE, href)
        if any(e["url"] == full_url for e in events):
            continue

        events.append({
            "url": full_url,
            "name": name,
            "upcoming": upcoming,
        })

    print(f"  Found {len(events)} {label} events")
    return events[:max_events]


def parse_event_page(url: str, event_name: str, upcoming: bool) -> dict | None:
    """Parse a single UFC event detail page from ufcstats.com."""
    soup = fetch(url)
    if not soup:
        return None

    # --- Event metadata ---
    # Name from the <h2> or passed in
    h2 = soup.select("h2")
    if h2:
        event_name = h2[0].get_text(strip=True) or event_name

    # Split name into name + subtitle at common separators
    subtitle = ""
    for sep in [":", " - "]:
        if sep in event_name:
            parts = event_name.split(sep, 1)
            event_name = parts[0].strip()
            subtitle = parts[1].strip()
            break

    # Date — usually in a <li> element
    event_date = ""
    lis = soup.select("li.b-list__box-list-item")
    for li in lis:
        text = li.get_text(strip=True)
        if "Date:" in text:
            date_str = text.split(":")[-1].strip()
            # Parse "Month DD, YYYY" format
            try:
                from datetime import datetime
                dt = datetime.strptime(date_str, "%B %d, %Y")
                event_date = dt.strftime("%Y-%m-%d")
            except (ValueError, ImportError):
                event_date = date_str
            break

    # Location/venue
    venue = ""
    for li in lis:
        text = li.get_text(strip=True)
        if "Location:" in text:
            venue = text.split(":", 1)[-1].strip()
            break

    # --- Fights ---
    fights = []

    if upcoming:
        # Upcoming events: look for matchup links
        # ufcstats upcoming format: rows with fighter name pairs
        fight_rows = soup.select("tr.b-fight-details__table-row")
        if not fight_rows:
            # Alternative: look for the simpler matchup display
            fight_rows = soup.select("tr")

        for row in fight_rows:
            # Each row has fighter names in <a> or <p> tags
            links = row.select("a.b-link.b-link_style_black")
            names = []
            for link in links:
                name = link.get_text(strip=True)
                if name and len(name) > 1:
                    names.append({
                        "name": name,
                        "profile": link.get("href", "").strip() or "",
                    })

            if len(names) >= 2:
                # Weight class — look for it in the row
                weight = ""
                row_text = row.get_text(" ", strip=True)
                weight_classes = [
                    "Strawweight", "Flyweight", "Bantamweight", "Featherweight",
                    "Lightweight", "Welterweight", "Middleweight",
                    "Light Heavyweight", "Heavyweight",
                    "Women's Strawweight", "Women's Flyweight",
                    "Women's Bantamweight", "Women's Featherweight",
                ]
                for wc in weight_classes:
                    if wc.lower() in row_text.lower():
                        weight = wc
                        break

                label = f"{weight} MMA" if weight else "MMA"

                fights.append({
                    "fighter_a": names[0],
                    "fighter_b": names[1],
                    "label": label,
                    "weight": weight,
                    "result": None,
                })
    else:
        # Completed events: rows have result data
        fight_rows = soup.select("tr.b-fight-details__table-row")

        for row in fight_rows:
            links = row.select("a.b-link.b-link_style_black")
            names = []
            for link in links:
                name = link.get_text(strip=True)
                if name and len(name) > 1:
                    names.append({
                        "name": name,
                        "profile": link.get("href", "").strip() or "",
                    })

            if len(names) < 2:
                continue

            # Check for win/loss indicators
            win_flags = row.select("a.b-flag")
            winner = None
            if win_flags:
                flag_text = win_flags[0].get_text(strip=True).lower()
                if "win" in flag_text:
                    winner = "a"  # First fighter listed is the winner on ufcstats

            # Method and round from row cells
            cells = row.select("td")
            method = "Decision"
            round_num = None
            row_text = row.get_text(" ", strip=True).lower()

            if any(k in row_text for k in ["ko", "tko"]):
                method = "KO/TKO"
            elif "sub" in row_text:
                method = "Submission"
            elif "dec" in row_text:
                method = "Decision"
            elif "draw" in row_text or "nc" in row_text:
                method = "Draw" if "draw" in row_text else "No Contest"
                winner = "draw"

            # Try to find round number
            rm = re.search(r"\b(\d)\b", " ".join(
                c.get_text(strip=True) for c in cells[-2:] if c.get_text(strip=True).isdigit()
            )) if cells else None
            if rm:
                round_num = int(rm.group(1))

            # Weight class
            weight = ""
            weight_classes = [
                "Strawweight", "Flyweight", "Bantamweight", "Featherweight",
                "Lightweight", "Welterweight", "Middleweight",
                "Light Heavyweight", "Heavyweight",
                "Women's Strawweight", "Women's Flyweight",
                "Women's Bantamweight", "Women's Featherweight",
            ]
            full_row_text = row.get_text(" ", strip=True)
            for wc in weight_classes:
                if wc.lower() in full_row_text.lower():
                    weight = wc
                    break

            label = f"{weight} MMA" if weight else "MMA"

            result = None
            if winner:
                result = {
                    "winner": winner,
                    "method": method,
                    "round": round_num,
                }

            fights.append({
                "fighter_a": names[0],
                "fighter_b": names[1],
                "label": label,
                "weight": weight,
                "result": result,
            })

    if not fights:
        return None

    # Build output in the same format as the ONE scraper
    structured_fights = []
    for i, fight in enumerate(fights):
        fa = fight["fighter_a"]
        fb = fight["fighter_b"]

        structured_fights.append({
            "id": i + 1,
            "label": fight["label"],
            "discipline": "mma",  # UFC is all MMA
            "main_event": i == 0,
            "fighter_a": {
                "name": fa["name"],
                "country": "",
                "flag": "",
                "record": "",
                "profile": fa.get("profile", ""),
            },
            "fighter_b": {
                "name": fb["name"],
                "country": "",
                "flag": "",
                "record": "",
                "profile": fb.get("profile", ""),
            },
            "result": fight.get("result"),
        })

    has_results = any(f.get("result") for f in structured_fights)

    return {
        "name": event_name,
        "subtitle": subtitle,
        "date": event_date,
        "venue": venue,
        "url": url,
        "promotion": "UFC",
        "status": "completed" if has_results else "upcoming",
        "fight_count": len(structured_fights),
        "fights": structured_fights,
    }


def enrich_fighter(fighter: dict, delay: float = 0.3):
    """Fetch a fighter's profile page on ufcstats.com for record + country."""
    profile_url = fighter.get("profile", "")
    if not profile_url or not profile_url.startswith("http"):
        return

    print(f"    Enrich: {fighter['name']}...", end=" ", flush=True)
    soup = fetch(profile_url)
    if not soup:
        print("failed")
        return

    text = soup.get_text(" ", strip=True)

    # Record: "Record: 29-0-0" or "W-L-D" style
    m = re.search(r"Record:\s*(\d{1,3}-\d{1,3}-\d{1,3})", text)
    if m:
        fighter["record"] = m.group(1)
    else:
        m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,3})", text)
        if m:
            fighter["record"] = m.group(0)

    # Country/nationality — ufcstats doesn't always show it prominently
    # Look for location-like text after common labels
    for label_text in ["Fighting out of", "fights out of"]:
        idx = text.lower().find(label_text.lower())
        if idx != -1:
            snippet = text[idx:idx+120]
            # Check for known countries
            sorted_countries = sorted(COUNTRY_FLAGS.keys(), key=len, reverse=True)
            for country in sorted_countries:
                if country in snippet:
                    fighter["country"] = country
                    fighter["flag"] = COUNTRY_FLAGS.get(country, "")
                    break
            if fighter["country"]:
                break

    # Fallback: scan broader text for country names
    if not fighter.get("country"):
        sorted_countries = sorted(COUNTRY_FLAGS.keys(), key=len, reverse=True)
        for country in sorted_countries:
            if country in text:
                fighter["country"] = country
                fighter["flag"] = COUNTRY_FLAGS.get(country, "")
                break

    status = fighter.get("record", "") or "no record"
    if fighter.get("country"):
        status += f", {fighter['country']}"
    print(status)

    time.sleep(delay)


def enrich_fighters(event: dict, delay: float = 0.3):
    """Enrich all fighters in an event with records and countries."""
    for fight in event["fights"]:
        for side in ("fighter_a", "fighter_b"):
            enrich_fighter(fight[side], delay)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data", help="Output directory")
    parser.add_argument("--enrich", action="store_true",
                        help="Fetch fighter profiles for records (slower)")
    parser.add_argument("--max-upcoming", type=int, default=4,
                        help="Max upcoming events to scrape")
    parser.add_argument("--max-completed", type=int, default=2,
                        help="Max recent completed events to scrape")
    args = parser.parse_args()

    print("=== Ringside UFC Scraper ===")

    # Get both upcoming and recently completed events
    upcoming = discover_events(upcoming=True, max_events=args.max_upcoming)
    completed = discover_events(upcoming=False, max_events=args.max_completed)

    results = []

    for ev in upcoming:
        print(f"\n  [upcoming] {ev['name']}")
        parsed = parse_event_page(ev["url"], ev["name"], upcoming=True)
        if parsed:
            print(f"  -> {parsed['fight_count']} fights")
            if args.enrich:
                enrich_fighters(parsed)
            results.append(parsed)
        else:
            print("  -> no fights found, skipping")

    for ev in completed:
        print(f"\n  [completed] {ev['name']}")
        parsed = parse_event_page(ev["url"], ev["name"], upcoming=False)
        if parsed:
            print(f"  -> {parsed['fight_count']} fights")
            if args.enrich:
                enrich_fighters(parsed)
            results.append(parsed)
        else:
            print("  -> no fights found, skipping")

    # Safety net: never overwrite good data with an empty scrape.
    if not results:
        print("\n[!] Scraped 0 UFC events — keeping previous data/ufc_events.json. "
              "ufcstats.com may be blocking the scraper (bot check) or changed layout.",
              file=sys.stderr)
        sys.exit(1)

    # Write output
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "ufc_events.json"

    output = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "ufcstats.com",
        "promotion": "UFC",
        "event_count": len(results),
        "events": results,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Wrote {len(results)} UFC events to {out_file}")


if __name__ == "__main__":
    main()
