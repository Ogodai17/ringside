"""
Discord feed test — runs scraper/discord_feed.py against a fake webhook.

Usage: python3 tests/discord_feed_test.py   (from the repo root, no extra packages)

Checks that the feed posts the right things, respects Discord's size limits,
remembers what it posted, never double-posts, and doesn't mark anything as
posted when every webhook fails.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scraper"))
import discord_feed as feed  # noqa: E402

RESULTS = []


def check(cond, name, detail=""):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {detail}"), flush=True)


class Hook(http.server.BaseHTTPRequestHandler):
    received: list = []
    status = 204

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Hook.received.append((self.path, json.loads(body)))
        self.send_response(Hook.status)
        self.end_headers()

    def log_message(self, *a):
        pass


def run(today, state, hooks, extra=()):
    env = dict(os.environ, DISCORD_WEBHOOKS=hooks)
    return subprocess.run([sys.executable, str(REPO / "scraper" / "discord_feed.py"),
                           "--today", today, "--state", str(state), *extra],
                          capture_output=True, text=True, env=env)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    hook_http = f"http://127.0.0.1:{port}/hookA"  # the feed accepts plain http only for 127.0.0.1

    # Pick a "today" from the data so the test doesn't rot: the day after the first completed event.
    events = feed.load_events()
    completed = [e for e in events if e.get("status") == "completed" and feed.event_day(e)]
    upcoming = [e for e in events if e.get("status") != "completed" and feed.event_day(e)]
    check(bool(completed) and bool(upcoming), "data has both completed and upcoming events")
    today = max(feed.event_day(e) for e in completed)
    today = today.fromordinal(today.toordinal() + 1).isoformat()

    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state.json"

        # 1. dry run: prints, sends nothing, writes no state
        r = run(today, state, hook_http, ["--dry-run"])
        check(r.returncode == 0, "dry run exits 0", r.stderr[-300:])
        check("This week's fight cards" in r.stdout or "Results are in" in r.stdout, "dry run prints a post")
        check(not state.exists() and not Hook.received, "dry run sends nothing and saves nothing")

        # 2. real run against the fake webhook (two hooks: one good, one bad path)
        r = run(today, state, f"{hook_http}\n{hook_http.replace('hookA', 'hookB')}")
        check(r.returncode == 0, "post run exits 0", r.stderr[-300:] + r.stdout[-300:])
        kinds = {p["embeds"][0]["title"] for _, p in Hook.received}
        check(len(Hook.received) >= 2, "something was posted", str(len(Hook.received)))
        check({"/hookA", "/hookB"} == {path for path, _ in Hook.received}, "every webhook got the post")
        for _, p in Hook.received:
            e = p["embeds"][0]
            check(len(e["description"]) <= 4096 and len(e["title"]) <= 256, "embed within Discord limits")
            check(p["allowed_mentions"] == {"parse": []}, "no @everyone/@here possible")
            check("pickafight.now" in e["description"] or "pickafight.now" in e.get("url", ""), "links back to the site")
        check("Results are in" in kinds, "results post was made (day after an event)", str(kinds))

        saved = json.loads(state.read_text())
        check(any(k.startswith("results:") for k in saved["posted"]), "results remembered in state")

        # 3. second run on the same day posts nothing
        before = len(Hook.received)
        r = run(today, state, hook_http)
        check(r.returncode == 0 and len(Hook.received) == before, "no double-posting on rerun", r.stdout[-200:])

        # 4. failing webhook → nothing marked as posted, exit code 1
        Hook.status = 500
        state2 = Path(td) / "state2.json"
        r = run(today, state2, hook_http)
        Hook.status = 204
        saved2 = json.loads(state2.read_text()) if state2.exists() else {"posted": {}}
        check(r.returncode == 1 and not saved2["posted"], "failed delivery is not marked as posted", r.stdout[-200:])

        # 5. no webhooks configured → skip cleanly, state untouched
        r = run(today, Path(td) / "state3.json", "")
        check(r.returncode == 0 and "skipping" in r.stdout, "missing DISCORD_WEBHOOKS skips without error")

    # 6. robbery maths mirrors the site (60% of 10+ cards for the loser)
    f = {"result": {"winner": "a", "method": "Decision"}, "fighter_a": {"name": "A"}, "fighter_b": {"name": "B"}}
    check("robbery" in feed.robbery_tag(f, {"a": 3, "b": 7}), "60%+ for the loser = robbery")
    check("Split" in feed.robbery_tag(f, {"a": 5, "b": 5}), "40–60% = split room")
    check(feed.robbery_tag(f, {"a": 2, "b": 6}) == "", "under 10 cards = no verdict")
    check(feed.robbery_tag({**f, "result": {"winner": "a", "method": None, "finish": True}}, {"a": 0, "b": 20}) == "",
          "finishes never get a robbery tag")

    srv.shutdown()
    bad = RESULTS.count(False)
    print(f"\n{len(RESULTS) - bad} passed, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
