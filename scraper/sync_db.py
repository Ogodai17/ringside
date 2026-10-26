#!/usr/bin/env python3
"""
Push fights, odds, start times and results into the Pick A Fight database,
then settle finished picks. Runs in GitHub Actions after the scrapers.

Needs:
  site_config.json  → database_url (the Supabase project URL)
  env SUPABASE_SECRET_KEY → the project's SECRET key, stored as a GitHub
                            repository secret (never in the repo or the site)

Without both, it prints a note and exits cleanly.

Fight keys must match the browser exactly (fightKey() in templates/index.html):
  'f' + base36(abs(int32 hash of "promotion|event name|fighter A|fighter B"))
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import site_config  # noqa: E402


def js_hash(s: str) -> int:
    """Port of the site's hashName(): Java-style 31x hash over UTF-16 code units, int32 wrap."""
    h = 0
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        c = data[i] | (data[i + 1] << 8)
        h = ((h << 5) - h + c) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h)


def base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def fight_key(promotion: str, event_name: str, a: str, b: str) -> str:
    return "f" + base36(js_hash(f"{promotion}|{event_name}|{a}|{b}"))


def discipline(d: str) -> str:
    if d == "mt":
        return "mt"
    if d in ("kb", "kickboxing"):
        return "kb"
    if d == "grappling":
        return "grappling"
    return "mma"


def rows_from_data():
    rows = []
    for name, default_promo in (("events.json", "ONE"), ("ufc_events.json", "UFC")):
        p = ROOT / "data" / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            promo = ev.get("promotion") or data.get("promotion") or default_promo
            ev_name = ev.get("name") or "Event"
            date = ev.get("date") if re.match(r"^\d{4}-\d{2}-\d{2}$", ev.get("date") or "") else None
            starts = ev.get("start") or (f"{date}T00:00:00Z" if date else None)
            for f in ev.get("fights", []):
                a, b = (f.get("fighter_a") or {}).get("name"), (f.get("fighter_b") or {}).get("name")
                odds = f.get("odds") or []
                if not (a and b and len(odds) == 2 and all(isinstance(x, (int, float)) and x > 1 for x in odds)):
                    continue
                row = {
                    "fight_key": fight_key(promo, ev_name, a, b),
                    "promotion": promo, "event_name": ev_name, "event_date": date, "starts_at": starts,
                    "fighter_a": a, "fighter_b": b,
                    "discipline": discipline(f.get("discipline", "mma")),
                    "rounds": 5 if re.search(r"5 rds|world champ|title", f.get("label") or "", re.I) else 3,
                    "odds_a": round(odds[0], 2), "odds_b": round(odds[1], 2),
                }
                r = f.get("result")
                if r and r.get("winner") in ("a", "b", "draw"):
                    method = r.get("method")
                    if method == "Draw":
                        row["result_winner"] = "draw"
                    else:
                        row["result_winner"] = r["winner"]
                    row["result_method"] = method
                    row["result_round"] = r.get("round")
                    fin = r.get("finish")
                    if fin is None and method:
                        fin = method != "Decision" and method != "Draw"
                    row["result_finish"] = fin
                rows.append(row)
    return rows


def call(url, key, path, body, prefer=None):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url + path, method="POST", data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode() or "null"
        return json.loads(raw)


def main():
    cfg = site_config.load()
    url = (cfg.get("database_url") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not (url and key):
        print("Database sync not configured (needs database_url in site_config.json + SUPABASE_SECRET_KEY secret) — skipping")
        return
    rows = rows_from_data()
    with_result = [r for r in rows if "result_winner" in r]
    without = [r for r in rows if "result_winner" not in r]
    upsert = "resolution=merge-duplicates,return=minimal"
    # Two batches: rows without a result never overwrite a stored result with null.
    for batch in (without, with_result):
        if batch:
            call(url, key, "/rest/v1/fights?on_conflict=fight_key", batch, prefer=upsert)
    settled = call(url, key, "/rest/v1/rpc/settle_all", {})
    print(f"✓ Synced {len(rows)} fights ({len(with_result)} with results); settled {settled} picks")
    print(f"::notice title=Database sync::{len(rows)} fights synced, {settled} picks settled")


if __name__ == "__main__":
    main()
