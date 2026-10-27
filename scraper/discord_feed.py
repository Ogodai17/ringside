"""
Discord feed — posts upcoming cards and settled results to Discord.

Feed only: it never takes picks from Discord. Its job is to put the week's
fights (and the robbery meter after decisions) in front of MMA servers with
a link back to the site.

Runs inside the daily GitHub Actions job after the scrapers, so it only ever
reads the repo's own data files. Posting goes through Discord *webhooks* —
a server admin makes one in Server Settings → Integrations → Webhooks and
you paste the URL into the DISCORD_WEBHOOKS repository secret (one per line
or comma-separated). No bot application or hosting needed.

What gets posted (one message per kind per run, so a run never spams):
  • "This week's cards" — every event starting within WINDOW_DAYS that
    hasn't been announced yet: date, venue, headline bouts with odds, and
    links to the preview page and the pick page.
  • "Results" — every event that has finished and has results in the data:
    winners, method and round for the main card. If the scorecard database
    is configured, decisions get the fan verdict: "Fans say robbery · 72%".

Already-posted events are remembered in data/discord_feed_state.json, which
the workflow commits with the rest of data/. Delete an entry there to repost.

Usage:
  python scraper/discord_feed.py                # post (needs DISCORD_WEBHOOKS)
  python scraper/discord_feed.py --dry-run      # print what would be posted
  python scraper/discord_feed.py --today 2026-10-31   # pretend it's that day
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

import site_config  # noqa: E402
from generate_previews import slugify  # noqa: E402
from sync_db import fight_key  # noqa: E402

DATA_FILES = (("events.json", "ONE"), ("ufc_events.json", "UFC"))
STATE_PATH = ROOT / "data" / "discord_feed_state.json"

WINDOW_DAYS = 7          # announce cards starting within this many days
RESULTS_MAX_AGE_DAYS = 4 # post results only for events this recent
MAX_BOUTS_CARD = 6       # bouts listed per event in the cards post
MAX_BOUTS_RESULTS = 6    # bouts listed per event in the results post
FAN_MIN = 10             # fan cards needed before calling a robbery (same as the site)
ROBBERY_SHARE = 0.6      # share of fans backing the loser = robbery (same as the site)
SPLIT_SHARE = 0.4        # 40–60% = split room

USERNAME = "Pick A Fight"
COLOR_CARDS = 0xE8403C   # site red
COLOR_RESULTS = 0x2E7D32


# ---------- data ----------

def load_events() -> list[dict]:
    events = []
    for name, default_promo in DATA_FILES:
        p = ROOT / "data" / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            ev = dict(ev)
            ev["promotion"] = ev.get("promotion") or data.get("promotion") or default_promo
            events.append(ev)
    return events


def event_title(ev: dict) -> str:
    name, sub = ev.get("name", "Event"), ev.get("subtitle", "")
    return f"{name}: {sub}" if sub else name


def event_slug(ev: dict) -> str:
    return slugify(event_title(ev))


def event_day(ev: dict) -> date | None:
    try:
        return date.fromisoformat(ev.get("date", "")[:10])
    except ValueError:
        return None


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"posted": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------- formatting ----------

def fmt_date(d: date | None) -> str:
    return d.strftime("%a %b %-d") if d else "TBA"


def bout_line(f: dict, with_odds: bool = True) -> str:
    a, b = f.get("fighter_a", {}), f.get("fighter_b", {})
    line = f"{a.get('flag', '')} **{a.get('name', '?')}** vs **{b.get('name', '?')}** {b.get('flag', '')}".strip()
    odds = f.get("odds") or []
    if with_odds and len(odds) == 2:
        line += f" · {odds[0]:.2f} / {odds[1]:.2f}"
    label = f.get("label")
    if label:
        line += f" · {label}"
    if f.get("main_event"):
        line = "⭐ " + line
    return line


def result_line(f: dict, verdict: dict | None) -> str:
    r = f.get("result") or {}
    a, b = f.get("fighter_a", {}), f.get("fighter_b", {})
    w = r.get("winner")
    if w == "a":
        who, loser = a.get("name", "?"), b.get("name", "?")
    elif w == "b":
        who, loser = b.get("name", "?"), a.get("name", "?")
    else:
        return f"🤝 {a.get('name', '?')} vs {b.get('name', '?')} — draw / no contest"
    how = r.get("method") or ("finish" if r.get("finish") else "decision")
    rnd = f" R{int(r['round'])}" if r.get("round") else ""
    line = f"✅ **{who}** def. {loser} — {how}{rnd}"
    tag = robbery_tag(f, verdict)
    if tag:
        line += f"\n   {tag}"
    return line


def robbery_tag(f: dict, verdict: dict | None) -> str:
    """Mirror of the site's robbery(): decisions only, FAN_MIN cards, loser share."""
    r = f.get("result") or {}
    if not verdict or r.get("method") != "Decision" or r.get("winner") not in ("a", "b"):
        return ""
    n = sum(verdict.values())
    if n < FAN_MIN:
        return ""
    loser = "b" if r["winner"] == "a" else "a"
    share = verdict.get(loser, 0) / n
    name = (f["fighter_b"] if loser == "b" else f["fighter_a"]).get("name", "the other guy")
    if share >= ROBBERY_SHARE:
        return f"🚨 **Fans say robbery** · {share:.0%} of {n} scorecards had {name} winning"
    if share >= SPLIT_SHARE:
        return f"⚖️ Split room · {share:.0%} of {n} scorecards had {name} winning"
    return f"👍 Fans agree · {1 - share:.0%} of {n} scorecards"


def cards_embed(events: list[dict], site_url: str) -> dict:
    parts = []
    for ev in events:
        d = event_day(ev)
        slug = event_slug(ev)
        fights = ev.get("fights", [])
        head = f"**{event_title(ev)}**\n{fmt_date(d)} · {ev.get('venue') or ev['promotion']} · {len(fights)} bouts"
        bouts = [bout_line(f) for f in fights[:MAX_BOUTS_CARD]]
        if len(fights) > MAX_BOUTS_CARD:
            bouts.append(f"…and {len(fights) - MAX_BOUTS_CARD} more")
        links = f"[Preview]({site_url}previews/{slug}.html) · [Pick this card]({site_url})"
        parts.append(head + "\n" + "\n".join(bouts) + "\n" + links)
    desc = "\n\n".join(parts)
    return {
        "title": "This week's fight cards",
        "url": site_url,
        "color": COLOR_CARDS,
        "description": clamp(desc, 4000),
        "footer": {"text": "Odds are decimal, from fighter records. Fake money, free to play. Start a league with your server."},
    }


def results_embed(events: list[dict], verdicts: dict[str, dict], site_url: str) -> dict:
    parts = []
    for ev in events:
        fights = [f for f in ev.get("fights", []) if f.get("result")]
        head = f"**{event_title(ev)}** — results"
        lines = []
        for f in fights[:MAX_BOUTS_RESULTS]:
            key = fight_key(ev["promotion"], ev.get("name", "Event"),
                            f["fighter_a"].get("name", ""), f["fighter_b"].get("name", ""))
            lines.append(result_line(f, verdicts.get(key)))
        if len(fights) > MAX_BOUTS_RESULTS:
            lines.append(f"…and {len(fights) - MAX_BOUTS_RESULTS} more on the site")
        links = f"[Full card + People's Scorecard]({site_url}previews/{event_slug(ev)}.html)"
        parts.append(head + "\n" + "\n".join(lines) + "\n" + links)
    desc = "\n\n".join(parts)
    return {
        "title": "Results are in",
        "url": site_url,
        "color": COLOR_RESULTS,
        "description": clamp(desc, 4000),
        "footer": {"text": "Picks settled. Think the judges got one wrong? Score it on the People's Scorecard."},
    }


def clamp(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---------- selection ----------

def pick_cards(events: list[dict], state: dict, today: date) -> list[dict]:
    out = []
    for ev in events:
        if ev.get("status") == "completed":
            continue
        d = event_day(ev)
        if not d or d < today or (d - today).days > WINDOW_DAYS:
            continue
        if "card:" + event_slug(ev) in state["posted"]:
            continue
        if not ev.get("fights"):
            continue
        out.append(ev)
    out.sort(key=lambda e: (e.get("date", ""), e.get("name", "")))
    return out


def pick_results(events: list[dict], state: dict, today: date) -> list[dict]:
    out = []
    for ev in events:
        fights = ev.get("fights", [])
        if ev.get("status") != "completed" or not fights:
            continue
        d = event_day(ev)
        # Only fresh results: the first run after switching the feed on
        # shouldn't dump a month of old cards into the channel.
        if not d or (today - d).days > RESULTS_MAX_AGE_DAYS:
            continue
        with_result = sum(1 for f in fights if f.get("result"))
        # Wait until most of the card has settled so one early result doesn't post a half card.
        if with_result < max(1, len(fights) // 2):
            continue
        if "results:" + event_slug(ev) in state["posted"]:
            continue
        out.append(ev)
    out.sort(key=lambda e: (e.get("date", ""), e.get("name", "")))
    return out


# ---------- fan verdicts (optional, needs the scorecard database) ----------

def fetch_verdicts(events: list[dict], cfg: dict) -> dict[str, dict]:
    """{fight_key: {"a": n, "b": n, "draw": n}} from fan_verdicts(); {} if no database."""
    try:
        api = site_config.db_api(cfg)
    except SystemExit:
        api = None
    if not api:
        return {}
    keys = []
    for ev in events:
        for f in ev.get("fights", []):
            if (f.get("result") or {}).get("method") == "Decision":
                keys.append(fight_key(ev["promotion"], ev.get("name", "Event"),
                                      f["fighter_a"].get("name", ""), f["fighter_b"].get("name", "")))
    out: dict[str, dict] = {}
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        h = {"apikey": api["key"], "Content-Type": "application/json"}
        if api["key"].startswith("eyJ"):
            h["Authorization"] = "Bearer " + api["key"]
        req = urllib.request.Request(api["url"] + "/rest/v1/rpc/fan_verdicts", method="POST",
                                     data=json.dumps({"p_fights": chunk}).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"fan verdicts unavailable ({e}); posting results without the robbery meter")
            return {}
        for row in rows:
            out.setdefault(row["fight_key"], {})[row["winner"]] = int(row["n"])
    return out


# ---------- posting ----------

def webhooks_from_env() -> list[str]:
    raw = os.environ.get("DISCORD_WEBHOOKS", "")
    urls = [u.strip() for part in raw.splitlines() for u in part.split(",")]
    # Real webhooks are https; plain http is only accepted for a local test server.
    return [u for u in urls if u.startswith("https://") or u.startswith(("http://127.0.0.1", "http://localhost"))]


def post(webhook: str, embed: dict, site_url: str) -> bool:
    payload = {
        "username": USERNAME,
        "avatar_url": f"{site_url}assets/icon-512.png",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    req = urllib.request.Request(webhook, method="POST", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "PickAFightFeed/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        print(f"webhook {redact(webhook)} rejected: HTTP {e.code} {e.read()[:200]!r}")
    except (urllib.error.URLError, OSError) as e:
        print(f"webhook {redact(webhook)} unreachable: {e}")
    return False


def redact(url: str) -> str:
    return url[:40] + "…" if len(url) > 40 else url


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the posts instead of sending; state untouched")
    ap.add_argument("--today", help="YYYY-MM-DD to use as today (testing)")
    ap.add_argument("--state", help="path to the state file (testing)")
    args = ap.parse_args()

    global STATE_PATH
    if args.state:
        STATE_PATH = Path(args.state)

    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    cfg = site_config.load()
    site_url = cfg["site_url"]
    events = load_events()
    state = load_state()

    cards = pick_cards(events, state, today)
    results = pick_results(events, state, today)
    posts: list[tuple[str, list[str], dict]] = []
    if cards:
        posts.append(("cards", ["card:" + event_slug(e) for e in cards], cards_embed(cards, site_url)))
    if results:
        verdicts = fetch_verdicts(results, cfg)
        posts.append(("results", ["results:" + event_slug(e) for e in results], results_embed(results, verdicts, site_url)))

    if not posts:
        print(f"nothing new to post for {today}")
        return 0

    if args.dry_run:
        for kind, keys, embed in posts:
            print(f"--- {kind} ({len(keys)} events) ---")
            print(embed["title"])
            print(embed["description"])
            print(embed["footer"]["text"])
        return 0

    hooks = webhooks_from_env()
    if not hooks:
        print("DISCORD_WEBHOOKS not set — skipping (state untouched, so nothing is lost)")
        return 0

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    failures = 0
    for kind, keys, embed in posts:
        delivered = sum(1 for h in hooks if post(h, embed, site_url))
        print(f"{kind}: delivered to {delivered}/{len(hooks)} webhooks")
        if delivered:
            for k in keys:
                state["posted"][k] = stamp
        else:
            failures += 1
    # Keep the state file from growing forever: drop entries older than 90 days.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    save_state(state)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
