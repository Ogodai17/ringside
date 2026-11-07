#!/usr/bin/env python3
"""
One-off check: how many of our upcoming fights does The Odds API price?

Reads ODDS_API_KEY from the environment (a GitHub secret — never printed).
Uses 1 free call (/sports, costs 0 credits) + 1 odds call
(mma_mixed_martial_arts, regions us,uk,eu, h2h market = 3 credits).
Prints GitHub ::notice lines with the coverage per event.

Usage: python scraper/odds_coverage.py data/events.json data/ufc_events.json
"""
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

API = "https://api.the-odds-api.com/v4"


def get(path, **params):
    params["apiKey"] = os.environ["ODDS_API_KEY"]
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        left = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        return json.load(r), left, used


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[^a-z]+", s) if len(w) > 1]


def same_person(a, b):
    """Loose match: share the last word, or share 2+ words (handles gym names / nicknames)."""
    wa, wb = norm(a), norm(b)
    if not wa or not wb:
        return False
    return wa[-1] == wb[-1] or len(set(wa) & set(wb)) >= 2


def main():
    if not os.environ.get("ODDS_API_KEY"):
        print("::error::ODDS_API_KEY is not set"); return 1

    sports, _, _ = get("/sports", all="true")
    combat = [s["key"] for s in sports if re.search(r"mma|box|kick|muay|ufc", s["key"] + s.get("title", ""), re.I)]
    print(f"::notice title=Odds API combat sports::{', '.join(combat) or 'none'}")

    games, left, used = get("/sports/mma_mixed_martial_arts/odds",
                            regions="us,uk,eu", markets="h2h", oddsFormat="decimal")
    print(f"::notice title=Odds API usage::{len(games)} MMA fights priced; credits used {used}, left {left}")

    for path in sys.argv[1:]:
        data = json.load(open(path))
        for ev in data.get("events", []):
            if ev.get("status") == "completed":
                continue
            fights = ev.get("fights", [])
            hits, misses = [], []
            for f in fights:
                a, b = f["fighter_a"]["name"], f["fighter_b"]["name"]
                g = next((g for g in games
                          if (same_person(a, g["home_team"]) and same_person(b, g["away_team"]))
                          or (same_person(a, g["away_team"]) and same_person(b, g["home_team"]))), None)
                label = f"{a} vs {b} ({f.get('discipline', '?')})"
                if g:
                    books = len(g.get("bookmakers", []))
                    hits.append(f"{label} [{books} books]")
                else:
                    misses.append(label)
            print(f"::notice title=Coverage {ev['name'][:60]}::{len(hits)}/{len(fights)} priced")
            for h in hits:
                print(f"  priced: {h}")
            for m in misses:
                print(f"  missing: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
