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
        if "/athletes/" in a["href"]:
            name = a.get_text(strip=True)
            if name and len(name) > 1:
                athlete_links.append({"name": name, "href": a["href"]})

    # Deduplicate preserving order
    seen_names = []
    unique = []
    for al in athlete_links:
        if al["name"] not in seen_names:
            seen_names.append(al["name"])
            unique.append(al)

    # Country info from tables
    countries = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) >= 3 and cells[1].lower() in ("country", "vs"):
                # cells[0] = fighter A's country, cells[2] = fighter B's country
                pass  # Countries parsed below from table context

    # Parse countries from the VS tables more carefully
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            cell_texts = [c.get_text(strip=True) for c in cells]
            if len(cell_texts) == 3 and cell_texts[1] == "Country":
                # This row has country info
                # Find which fighters this belongs to by looking at nearby athlete links
                parent = table.parent
                if parent:
                    local_links = parent.find_all("a", href=re.compile(r"/athletes/"))
                    local_names = []
                    for ll in local_links:
                        n = ll.get_text(strip=True)
                        if n and n not in local_names:
                            local_names.append(n)
                    if len(local_names) >= 2:
                        countries[local_names[0]] = cell_texts[0]
                        countries[local_names[1]] = cell_texts[2]

    # Pair fighters into bouts
    fights = []
    for i in range(0, len(unique) - 1, 2):
        fa = unique[i]
        fb = unique[i + 1]
        fight_idx = i // 2

        label = disc_labels[fight_idx] if fight_idx < len(disc_labels) else f"Bout {fight_idx+1}"
        disc = classify_discipline(label)

        country_a = countries.get(fa["name"], "")
        country_b = countries.get(fb["name"], "")
        flag_a = COUNTRY_FLAGS.get(country_a, "")
        flag_b = COUNTRY_FLAGS.get(country_b, "")

        fights.append({
            "id": fight_idx + 1,
            "label": label,
            "discipline": disc,
            "fighter_a": {
                "name": fa["name"],
                "country": country_a,
                "flag": flag_a,
                "profile": urljoin(BASE, fa["href"]),
            },
            "fighter_b": {
                "name": fb["name"],
                "country": country_b,
                "flag": flag_b,
                "profile": urljoin(BASE, fb["href"]),
            },
        })

    if not fights:
        return None

    # Mark main event
    if fights:
        fights[0]["main_event"] = True

    return {
        "name": event_name,
        "subtitle": subtitle,
        "venue": venue,
        "url": url,
        "fight_count": len(fights),
        "fights": fights,
    }


def enrich_records(event: dict, delay: float = 0.5):
    """Fetch each fighter's profile page to get their record."""
    for fight in event["fights"]:
        for side in ("fighter_a", "fighter_b"):
            fighter = fight[side]
            profile_url = fighter.get("profile", "")
            if not profile_url:
                continue

            print(f"    Record: {fighter['name']}...", end=" ", flush=True)
            soup = fetch(profile_url)
            if soup:
                text = soup.get_text()
                # Look for record in format like "13-0-0 (Win-Loss-Draw)"
                # or standalone "13-0-0"
                m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,3})", text)
                if m:
                    fighter["record"] = m.group(0)
                    print(fighter["record"])
                else:
                    fighter["record"] = ""
                    print("not found")
            else:
                fighter["record"] = ""
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
                enrich_records(parsed)

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
