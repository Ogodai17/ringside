#!/usr/bin/env python3
"""Offline checks for scraper/market_odds.py and its hand-off with odds.py."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import market_odds as mo  # noqa: E402
import odds  # noqa: E402

passed = failed = 0


def check(cond, name, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"[PASS] {name}")
    else:
        failed += 1; print(f"[FAIL] {name} — {detail}")


def book(a, pa, b, pb, key="h2h"):
    return {"markets": [{"key": key, "outcomes": [{"name": a, "price": pa}, {"name": b, "price": pb}]}]}


GAMES = [
    {"home_team": "Waldo Cortes-Acosta", "away_team": "Johnny Walker", "commence_time": "2026-11-08T03:00:00Z",
     "bookmakers": [book("Waldo Cortes-Acosta", 1.70, "Johnny Walker", 2.20),
                    book("Johnny Walker", 2.10, "Waldo Cortes-Acosta", 1.75),
                    book("Waldo Cortes-Acosta", 1.80, "Johnny Walker", 2.00),
                    book("Waldo Cortes-Acosta", 9.0, "Johnny Walker", 9.0, key="totals")]},
    # Same surnames as a fight on another date: must not match
    {"home_team": "Jose Silva", "away_team": "Mike Jones", "commence_time": "2026-12-20T03:00:00Z",
     "bookmakers": [book("Jose Silva", 1.5, "Mike Jones", 2.6)]},
]


def fight(a, b, result=None):
    return {"fighter_a": {"name": a, "record": "10-2-0"}, "fighter_b": {"name": b, "record": "9-3-0"},
            "odds": [1.9, 1.9], "p_a": 0.5, "result": result}


def data():
    return {"events": [
        {"name": "UFC Fight Night", "date": "2026-11-07", "status": "upcoming", "fights": [
            fight("Waldo Cortes Acosta", "Johnny Walker"),
            fight("Ana Silva", "Kate Jones"),          # surnames match game 2, but 6 weeks off
            fight("Unknown One", "Unknown Two"),
        ]},
        {"name": "UFC 333", "date": "2026-10-24", "status": "completed", "fights": [
            fight("Waldo Cortes Acosta", "Johnny Walker", result={"winner": "a"}),
        ]},
    ]}


d = data()
priced, upcoming = mo.apply(d, GAMES)
f = d["events"][0]["fights"][0]
check(priced == 1 and upcoming == 3, "Only the real match gets a market line", f"{priced}/{upcoming}")
check(f["odds"] == [1.75, 2.1], "Median price per fighter, from h2h only, order-independent", f["odds"])
check(f["odds_src"] == "market" and f["market_for"] == ["Waldo Cortes Acosta", "Johnny Walker"], "Fight is tagged as a market line", f)
check(0.5 < f["p_a"] < 0.6 and abs(f["p_a"] - (1/1.75) / (1/1.75 + 1/2.1)) < 0.001, "Win chance has the bookmaker margin removed", f["p_a"])
check(d["events"][0]["fights"][1]["odds"] == [1.9, 1.9], "Same surnames on a far-off date are not matched", d["events"][0]["fights"][1])
check(d["events"][1]["fights"][0].get("odds_src") is None, "Completed events are left alone", "")
check(mo.apply(copy.deepcopy(data()), [])[0] == 0, "Empty feed changes nothing", "")

arb = [{"home_team": "Cortes Acosta", "away_team": "Johnny Walker", "commence_time": "2026-11-08T03:00:00Z",
        "bookmakers": [book("Cortes Acosta", 2.10, "Johnny Walker", 2.10)]}]
d2 = data(); mo.apply(d2, arb); o = d2["events"][0]["fights"][0]["odds"]
check(1.0 <= 1/o[0] + 1/o[1] <= 1.05, "A below-fair median is nudged back to a small margin", o)

# odds.py keeps the market line on the next run...
odds.add_odds(d)
check(f["odds"] == [1.75, 2.1] and f["odds_src"] == "market", "odds.py keeps a market line for the same matchup", f)
check(d["events"][0]["fights"][2].get("odds_src") is None and d["events"][0]["fights"][2]["odds"] != [1.9, 1.9],
      "odds.py still sets record odds on unpriced fights", d["events"][0]["fights"][2])
# ...but drops it if the matchup changes (late replacement)
f["fighter_b"]["name"] = "Replacement Guy"
odds.add_odds(d)
check(f.get("odds_src") is None and "market_for" not in f and f["odds"] != [1.75, 2.1],
      "A changed matchup falls back to record odds", f)

# No key: does nothing, never fails
import os
os.environ.pop("ODDS_API_KEY", None)
check(mo.main(["/nonexistent.json"]) == 0, "No ODDS_API_KEY: exits cleanly", "")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
