#!/usr/bin/env python3
"""
Pick A Fight odds model — sets realistic winner odds on every fight.

Odds are computed ONCE here and stored in the data files
(fight["odds"] = [odds_a, odds_b], fight["p_a"] = win chance for A),
so the game and the preview pages always show the same numbers.

Model (simple, explainable, no outside data):
  strength = log((wins + 5) / (losses + 5)) + 0.3 * log(1 + bouts)
    - the +5s shrink small records toward even (a 2-0 prospect isn't
      treated like a 20-0 champion)
    - a little credit for experience
  P(A wins) = logistic(1.1 × (strength_A - strength_B)), clamped to 12%..88%
  Unknown record = average fighter, and the gap is halved (less certain).
  Decimal odds include a 6% margin, like a real sportsbook line.
  Unknown on both sides = a coin flip with a tiny name-based nudge, so a
  card of unknowns isn't a wall of identical 1.89s.

Usage: python scraper/odds.py data/events.json data/ufc_events.json
"""

import json
import math
import re
import sys
from pathlib import Path

MARGIN = 1.06
P_MIN, P_MAX = 0.12, 0.88


def parse_record(rec: str):
    m = re.match(r"^\s*(\d+)-(\d+)(?:-(\d+))?", rec or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def strength(rec):
    w, l, d = rec
    return math.log((w + 5) / (l + 5)) + 0.3 * math.log(1 + w + l + d)


def _name_nudge(a: str, b: str) -> float:
    h = 0
    for ch in f"{a}|{b}":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return ((h % 1000) / 1000 - 0.5) * 0.16  # ±0.08


def win_probability(rec_a: str, rec_b: str, name_a: str = "", name_b: str = "") -> float:
    ra, rb = parse_record(rec_a), parse_record(rec_b)
    if ra is None and rb is None:
        return 0.5 + _name_nudge(name_a, name_b)
    sa = strength(ra) if ra else strength((5, 5, 0))  # unknown ≈ average 5-5
    sb = strength(rb) if rb else strength((5, 5, 0))
    gap = sa - sb
    if ra is None or rb is None:
        gap *= 0.5
    p = 1 / (1 + math.exp(-gap * 1.1))
    return min(P_MAX, max(P_MIN, p))


def decimal_odds(p: float) -> float:
    return round(max(1.01, 1 / (p * MARGIN)), 2)


def add_odds(data: dict) -> int:
    n = 0
    for ev in data.get("events", []):
        for f in ev.get("fights", []):
            a, b = f.get("fighter_a") or {}, f.get("fighter_b") or {}
            p = win_probability(a.get("record", ""), b.get("record", ""), a.get("name", ""), b.get("name", ""))
            f["p_a"] = round(p, 3)
            f["odds"] = [decimal_odds(p), decimal_odds(1 - p)]
            n += 1
    return n


def main(paths):
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"  (skip missing {p})")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        n = add_odds(data)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ odds set on {n} fights in {p}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["data/events.json", "data/ufc_events.json"])
