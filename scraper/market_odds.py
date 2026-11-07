#!/usr/bin/env python3
"""
Real sportsbook lines for UFC fights, from The Odds API.

Runs after scraper/odds.py (record-based odds) on the daily scheduled run.
For every upcoming UFC fight the feed prices, it replaces the record odds
with the MEDIAN decimal price across bookmakers for each fighter, and marks
the fight `odds_src: "market"` (shown on the site as a small "market line"
tag). No bookmaker names are stored or shown. Fights the feed doesn't price
(all of ONE, as of Nov 2026) keep their record-based odds.

One API call per run: mma_mixed_martial_arts, regions us,uk,eu, h2h market
= 3 credits (free tier: 500/month). Without ODDS_API_KEY it does nothing.

odds.py keeps a market line on later runs (e.g. push runs that don't call
the API) as long as the same two fighters are still booked — see
`market_for`.

Usage: python scraper/market_odds.py data/ufc_events.json
"""
import json
import os
import re
import statistics
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds"


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[^a-z]+", s) if len(w) > 1]


def same_person(a, b):
    """Last word matches, or 2+ words in common (handles hyphens, middle names)."""
    wa, wb = norm(a), norm(b)
    if not wa or not wb:
        return False
    return wa[-1] == wb[-1] or len(set(wa) & set(wb)) >= 2


def near(date_str, commence):
    """Feed fight starts within 2 days of our event date (guards against a
    rematch or same-surname bout on another card)."""
    if not date_str or not commence:
        return True
    try:
        d = datetime.fromisoformat(date_str[:10]).date()
        c = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except ValueError:
        return True
    return abs((c - d).days) <= 2


def prices(game, fighter):
    """Every bookmaker's decimal price for this fighter (h2h market)."""
    out = []
    for bm in game.get("bookmakers", []):
        for m in bm.get("markets", []):
            if m.get("key") != "h2h":
                continue
            for o in m.get("outcomes", []):
                p = o.get("price")
                if isinstance(p, (int, float)) and p > 1 and same_person(fighter, o.get("name", "")):
                    out.append(float(p))
    return out


def apply(data, games):
    """Put market lines on matching upcoming fights. Returns (priced, upcoming)."""
    priced = upcoming = 0
    for ev in data.get("events", []):
        if ev.get("status") == "completed":
            continue
        for f in ev.get("fights", []):
            if f.get("result"):
                continue
            upcoming += 1
            a = (f.get("fighter_a") or {}).get("name", "")
            b = (f.get("fighter_b") or {}).get("name", "")
            g = next((g for g in games
                      if near(ev.get("date"), g.get("commence_time"))
                      and ((same_person(a, g.get("home_team", "")) and same_person(b, g.get("away_team", "")))
                           or (same_person(a, g.get("away_team", "")) and same_person(b, g.get("home_team", ""))))),
                     None)
            if not g:
                continue
            pa, pb = prices(g, a), prices(g, b)
            if not pa or not pb:
                continue
            oa, ob = round(statistics.median(pa), 2), round(statistics.median(pb), 2)
            if oa <= 1 or ob <= 1:
                continue
            tot = 1 / oa + 1 / ob
            if tot < 1.0:                    # medians can dip under a fair book;
                oa = round(max(1.01, oa * tot / 1.03), 2)   # never offer a free-money line
                ob = round(max(1.01, ob * tot / 1.03), 2)
            ia, ib = 1 / oa, 1 / ob          # implied chances, margin removed below
            f["odds"] = [oa, ob]
            f["p_a"] = round(ia / (ia + ib), 3)
            f["odds_src"] = "market"
            f["market_for"] = [a, b]
            priced += 1
    return priced, upcoming


def fetch():
    params = {"apiKey": os.environ["ODDS_API_KEY"], "regions": "us,uk,eu",
              "markets": "h2h", "oddsFormat": "decimal"}
    with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(params)}", timeout=30) as r:
        return json.load(r), r.headers.get("x-requests-remaining")


def main(paths):
    if not os.environ.get("ODDS_API_KEY"):
        print("  (no ODDS_API_KEY — keeping record-based odds)")
        return 0
    try:
        games, left = fetch()
    except Exception as e:  # never break the daily run over odds
        print(f"::warning title=Market odds::Odds API call failed ({type(e).__name__}); keeping record-based odds")
        return 0
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        priced, upcoming = apply(data, games)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"::notice title=Market odds::{p.name}: {priced}/{upcoming} upcoming fights on market lines; API credits left {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["data/ufc_events.json"]))
