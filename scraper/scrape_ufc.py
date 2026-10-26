#!/usr/bin/env python3
"""
Ringside UFC Scraper — pulls UFC fight cards from ESPN's public data feed
(site.api.espn.com) and writes data/ufc_events.json in the same format as
the ONE scraper.

Why ESPN: ufcstats.com started serving a JavaScript "checking your browser"
page in Oct 2026, which plain requests can't get past. ESPN's feed is JSON,
so there's no HTML to break. It's an unofficial endpoint (no published
terms for this use) — if it disappears, this is the file to change.

Run: python scraper/scrape_ufc.py --out data
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
HEADERS = {"User-Agent": "Mozilla/5.0 (Ringside fight predictions; +https://github.com/Ogodai17/ringside)"}
EASTERN = ZoneInfo("America/New_York")  # ESPN keys UFC dates to US Eastern

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

WEIGHT_LABELS = {"W ": "Women's "}


def get_json(url: str, params: dict | None = None, retries: int = 2) -> dict | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"  [!] Failed: {url} {params or ''} — {e}", file=sys.stderr)
    return None


def parse_iso(s: str) -> datetime:
    # ESPN uses "2026-10-24T17:00Z" (no seconds)
    s = s.replace("Z", "+00:00")
    if re.match(r".*T\d{2}:\d{2}\+", s):
        s = s.replace("+", ":00+", 1)
    return datetime.fromisoformat(s)


def event_id_from_ref(ref: str) -> str:
    m = re.search(r"/events/(\d+)", ref or "")
    return m.group(1) if m else ""


def get_calendar() -> list[dict]:
    """All UFC events this season: [{label, start (datetime), id}]."""
    data = get_json(SCOREBOARD)
    if not data:
        return []
    cal = []
    for entry in (data.get("leagues") or [{}])[0].get("calendar", []):
        label = entry.get("label", "")
        if not label.startswith("UFC"):  # skip Contender Series etc.
            continue
        try:
            start = parse_iso(entry["startDate"])
        except Exception:
            continue
        cal.append({"label": label, "start": start,
                    "id": event_id_from_ref((entry.get("event") or {}).get("$ref", ""))})
    return sorted(cal, key=lambda e: e["start"])


def fetch_event(cal_entry: dict) -> dict | None:
    """Fetch the scoreboard for the event's date and return that event."""
    start = cal_entry["start"]
    tried = []
    for d in (start.astimezone(EASTERN).date(), start.astimezone(timezone.utc).date()):
        key = d.strftime("%Y%m%d")
        if key in tried:
            continue
        tried.append(key)
        data = get_json(SCOREBOARD, {"dates": key})
        for ev in (data or {}).get("events", []):
            if cal_entry["id"] and ev.get("id") == cal_entry["id"]:
                return ev
            if not cal_entry["id"] and ev.get("name") == cal_entry["label"]:
                return ev
    return None


def country_flag(athlete: dict) -> tuple[str, str]:
    alt = ((athlete.get("flag") or {}).get("alt") or "").strip()
    return alt, COUNTRY_FLAGS.get(alt, "")


def weight_label(comp: dict) -> str:
    label = ((comp.get("type") or {}).get("abbreviation") or "").strip()
    for short, full in WEIGHT_LABELS.items():
        if label.startswith(short):
            label = full + label[len(short):]
    return label or "Bout"


def method_from_status(status: dict) -> str | None:
    """Best-effort method from whatever ESPN puts in a finished bout's status."""
    res = status.get("result") or {}
    text = " ".join(str(x) for x in [
        res.get("name"), res.get("displayName"), res.get("shortDisplayName"),
        res.get("description"), (status.get("type") or {}).get("detail"),
        (status.get("type") or {}).get("shortDetail"),
    ] if x).lower()
    if re.search(r"\b(t?ko|knockout|kotko)\b", text) or "ko/tko" in text:
        return "KO/TKO"
    if re.search(r"\b(sub|submission)\b", text):
        return "Submission"
    if re.search(r"\b(dec|decision|unanimous|split|majority)\b", text):
        return "Decision"
    if re.search(r"\bdraw\b", text):
        return "Draw"
    return None


_diag_printed = False


def parse_bout(comp: dict, idx: int) -> dict | None:
    global _diag_printed
    competitors = sorted(comp.get("competitors", []), key=lambda c: c.get("order", 0))
    if len(competitors) != 2:
        return None
    fighters = []
    for c in competitors:
        ath = c.get("athlete") or {}
        country, flag = country_flag(ath)
        record = next((r.get("summary", "") for r in c.get("records", [])
                       if r.get("type") == "total" or r.get("abbreviation") == "TOT"), "")
        fighters.append({
            "name": ath.get("displayName") or ath.get("fullName") or "TBA",
            "country": country,
            "flag": flag,
            "record": record,
            "profile": "",
        })

    status = comp.get("status") or {}
    stype = status.get("type") or {}
    result = None
    if stype.get("completed"):
        winner = next((("a", "b")[i] for i, c in enumerate(competitors) if c.get("winner")), None)
        method = method_from_status(status)
        if method == "Draw":
            winner = "draw"
        if winner:
            rnd = status.get("period") or None
            result = {"winner": winner, "method": method, "round": rnd if method != "Decision" else None}
        if not _diag_printed:
            # Shows up as an annotation on the GitHub Actions run, so the
            # result format can be checked without downloading logs.
            snippet = json.dumps({"status": status, "winners": [c.get("winner") for c in competitors]})[:900]
            print(f"::notice title=ESPN finished-bout sample::{snippet}")
            _diag_printed = True

    rounds = ((comp.get("format") or {}).get("regulation") or {}).get("periods")
    return {
        "id": idx,
        "label": weight_label(comp) + (" (5 rds)" if rounds == 5 else ""),
        "discipline": "mma",
        "fighter_a": fighters[0],
        "fighter_b": fighters[1],
        "result": result,
    }


def parse_event(ev: dict) -> dict | None:
    comps = ev.get("competitions", [])
    # ESPN lists the early prelims first; the frontend expects the main event first.
    comps = sorted(comps, key=lambda c: c.get("startDate") or c.get("date") or "", reverse=True)
    fights = [b for i, c in enumerate(comps) if (b := parse_bout(c, i + 1))]
    if not fights:
        return None

    full = ev.get("name", "UFC Event")
    name, _, subtitle = full.partition(":")
    # Main event = the bout matching the headline names, else the last-starting bout
    headline = [w.strip().lower() for w in subtitle.split(" vs. ")] if " vs. " in subtitle else []
    main_idx = 0
    for i, f in enumerate(fights):
        a, b = f["fighter_a"]["name"].lower(), f["fighter_b"]["name"].lower()
        if len(headline) == 2 and any(headline[0] in x for x in (a, b)) and any(headline[1] in x for x in (a, b)):
            main_idx = i
            break
    fights.insert(0, fights.pop(main_idx))
    for i, f in enumerate(fights):
        f["id"] = i + 1
    fights[0]["main_event"] = True

    start = parse_iso(ev["date"])
    venue = (ev.get("venues") or [None])[0] or (comps[0].get("venue") if comps else None) or {}
    addr = venue.get("address") or {}
    place = ", ".join(x for x in [venue.get("fullName"), addr.get("city"), addr.get("country") or addr.get("state")] if x)
    # Over = ESPN says so, or it started more than 12 hours ago
    completed = bool(((ev.get("status") or {}).get("type") or {}).get("completed")) or \
        (datetime.now(timezone.utc) - start).total_seconds() > 12 * 3600

    return {
        "name": name.strip(),
        "subtitle": subtitle.strip(),
        "date": start.astimezone(EASTERN).date().isoformat(),
        "venue": place,
        "url": next((l.get("href") for l in ev.get("links", []) if l.get("href")), ""),
        "status": "completed" if completed else "upcoming",
        "promotion": "UFC",
        "fight_count": len(fights),
        "fights": fights,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data", help="Output directory")
    parser.add_argument("--max-upcoming", type=int, default=4)
    parser.add_argument("--max-completed", type=int, default=2)
    parser.add_argument("--enrich", action="store_true", help="(no-op; ESPN already includes records/countries)")
    args = parser.parse_args()

    print("=== Ringside UFC Scraper (ESPN feed) ===")
    cal = get_calendar()
    if not cal:
        print("[!] Could not load the UFC calendar from ESPN.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    # An event counts as "upcoming" until ~12h after its listed start
    upcoming = [c for c in cal if (c["start"] - now).total_seconds() > -12 * 3600][: args.max_upcoming]
    completed = [c for c in cal if (c["start"] - now).total_seconds() <= -12 * 3600][-args.max_completed:][::-1]

    results = []
    for entry in upcoming + completed:
        print(f"\n  {entry['label']} ({entry['start'].date()})")
        ev = fetch_event(entry)
        if not ev:
            print("  -> not found on scoreboard, skipping")
            continue
        parsed = parse_event(ev)
        if parsed:
            done = sum(1 for f in parsed["fights"] if f["result"])
            print(f"  -> {parsed['fight_count']} fights, {parsed['status']}, {done} results")
            results.append(parsed)
        else:
            print("  -> no bouts listed yet, skipping")

    if not results:
        print("\n[!] Scraped 0 UFC events — keeping previous data/ufc_events.json.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "ufc_events.json"
    output = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "espn.com",
        "promotion": "UFC",
        "event_count": len(results),
        "events": results,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Wrote {len(results)} UFC events to {out_file}")
    print(f"::notice title=UFC scrape::{len(results)} events: " + "; ".join(
        f"{e['name']} {e['date']} {e['status']} {e['fight_count']} bouts, "
        f"{sum(1 for f in e['fights'] if f['result'])} results" for e in results))


if __name__ == "__main__":
    main()
