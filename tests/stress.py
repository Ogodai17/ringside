"""
Ringside stress test — runs the real built site in headless Chrome.

Usage: python3 tests/stress.py . [--shots <dir>]   (from the repo root)
Needs: pip install playwright && python -m playwright install chromium
Copies the site into a temp dir per scenario, serves it on localhost,
and attacks it. Prints PASS/FAIL/WARN lines and a summary.
"""
import http.server
import json
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(sys.argv[1]).resolve()
SHOTS = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else None
RESULTS = []


def record(status, name, detail=""):
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def check(cond, name, detail="", warn=False):
    record("PASS" if cond else ("WARN" if warn else "FAIL"), name, "" if cond else detail)
    return cond


# ---------- site copies + server ----------

def make_site(mutate=None):
    d = Path(tempfile.mkdtemp(prefix="rs_"))
    for name in ["index.html", "about.html", "contact.html", "privacy.html", "sitemap.xml"]:
        if (REPO / name).exists():
            shutil.copy(REPO / name, d / name)
    shutil.copytree(REPO / "data", d / "data")
    shutil.copytree(REPO / "previews", d / "previews")
    if mutate:
        mutate(d)
    return d


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory):
    handler = lambda *a, **k: Quiet(*a, directory=str(directory), **k)
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


class Page:
    """One browser page on one site copy, collecting JS errors."""

    def __init__(self, browser, site, storage=None, viewport=(1280, 900)):
        self.srv, self.url = serve(site)
        self.ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.p = self.ctx.new_page()
        self.errors = []
        self.p.on("pageerror", lambda e: self.errors.append(str(e)))
        self.p.on("console", lambda m: m.type == "error" and "ERR_TUNNEL" not in m.text and "ERR_NAME_NOT_RESOLVED" not in m.text and self.errors.append("console: " + m.text))
        self.p.on("dialog", lambda d: (self.errors.append("dialog: " + d.message), d.dismiss()))
        if storage is not None:
            self.p.add_init_script(f"localStorage.setItem('ringside_state', {json.dumps(storage)});")
        self.p.goto(self.url + "index.html")
        self.p.wait_for_timeout(700)

    def js(self, expr, arg=None):
        return self.p.evaluate(expr, arg)

    def state(self):
        return json.loads(self.js("localStorage.getItem('ringside_state')") or "{}")

    def reload(self):
        self.p.reload()
        self.p.wait_for_timeout(700)

    def close(self):
        self.ctx.close()
        self.srv.shutdown()


def first_open_fight(pg):
    return pg.js("""() => {
      for (let i=0;i<events.length;i++){ switchEvent(i);
        const b=document.querySelector('.fight-card:not(.fight-completed) .odd-btn:not([disabled])');
        if(b) return {ev:i, id:b.closest('.fight-card').id.replace('fight-','')}; }
      return null; }""")


def place_via_ui(pg, stake_text, side="A"):
    """Pick a fighter and place a stake by typing, like a user would."""
    pg.js("(id)=>selectWinner(id, %r)" % side, first_open_fight(pg)["id"])
    pg.p.fill("#stakeInput", "")
    pg.p.type("#stakeInput", str(stake_text))
    disabled = pg.js("document.getElementById('placeBetBtn').disabled")
    if not disabled:
        pg.p.click("#placeBetBtn")
        pg.p.wait_for_timeout(150)
    return disabled


# ---------- scenarios ----------

def t_load_and_render(browser):
    pg = Page(browser, make_site())
    n_events = pg.js("events.length")
    check(not pg.errors, "Page loads without JS errors", "; ".join(pg.errors[:3]))
    check(n_events >= 2, "Events loaded", f"{n_events} events")
    bad = pg.js("""() => { const out=[]; for(let i=0;i<events.length;i++){ switchEvent(i);
        const n=document.querySelectorAll('.fight-card').length; if(n!==events[i].fights.length) out.push(events[i].name+': '+n+'/'+events[i].fights.length);} return out; }""")
    check(not bad, "Every event tab renders all its fights", str(bad))
    names = pg.js("events.map(e=>e.name+' '+e.date+' '+e.status)")
    dates = pg.js("events.filter(e=>e.status!=='completed').map(e=>e.date)")
    check(dates == sorted(dates), "Upcoming events sorted soonest first", str(names))
    for tab in ["mybets", "leaderboard", "fights"]:
        pg.p.click(f'[data-tab="{tab}"]')
        pg.p.wait_for_timeout(100)
    check(not pg.errors, "Tabs switch without errors", "; ".join(pg.errors[:3]))
    pg.close()


def t_stakes(browser):
    pg = Page(browser, make_site())
    cases = [("-100", "negative"), ("0", "zero"), ("abc", "letters"), ("5001", "more than balance"),
             ("1e9", "scientific notation"), ("10.9", "decimal")]
    for text, label in cases:
        before = pg.js("balance")
        n_before = pg.js("bets.length")
        disabled = place_via_ui(pg, text)
        after = pg.js("balance")
        took = before - after
        if label == "decimal":
            check(took in (0, 10, 11) and after == int(after), f"Stake '{text}' ({label}) handled cleanly",
                  f"balance {before}→{after}")
        else:
            check(took == 0 and pg.js("bets.length") == n_before, f"Stake '{text}' ({label}) rejected",
                  f"balance {before}→{after}, button disabled={disabled}")
        pg.js("closeBetSlip()")
    # all-in
    before = pg.js("balance")
    place_via_ui(pg, before)
    check(pg.js("balance") == 0, "All-in stake works", f"balance left {pg.js('balance')}")
    check(not pg.errors, "No JS errors during stake tests", "; ".join(pg.errors[:3]))
    pg.close()


def t_double_submit(browser):
    pg = Page(browser, make_site())
    pg.js("(id)=>selectWinner(id,'A')", first_open_fight(pg)["id"])
    pg.p.fill("#stakeInput", "100")
    pg.js("updatePayout()")
    pg.js("placeBet(); placeBet(); placeBet();")  # rapid triple submit
    check(pg.js("bets.length") == 1 and pg.js("balance") == 4900, "Rapid double-submit places only one pick",
          f"{pg.js('bets.length')} picks, balance {pg.js('balance')}")
    # bet on more than balance via repeated small bets
    pg.reload()
    check(pg.state().get("balance") == 4900 and len(pg.state().get("bets", [])) == 1, "Pick persists after reload",
          str(pg.state())[:200])
    pg.close()


def t_topup(browser):
    pg = Page(browser, make_site(), storage=json.dumps({"balance": 50, "bets": [], "lastUBIClaim": None}))
    vis = pg.js("getComputedStyle(document.getElementById('ubi-banner')).display")
    check(vis == "block", "Daily top-up offered when balance < 300", f"display={vis}")
    pg.p.click("#ubi-banner")
    pg.p.wait_for_timeout(100)
    b1 = pg.js("balance")
    try:
        pg.p.click("#ubi-banner", timeout=500)
    except Exception:
        pass
    pg.reload()
    try:
        pg.p.click("#ubi-banner", timeout=500)
    except Exception:
        pass
    check(b1 == 350 and pg.js("balance") == 350, "Top-up can only be claimed once per day",
          f"after first claim {b1}, after retries {pg.js('balance')}")
    pg.close()


def t_corrupt_storage(browser):
    cases = {
        "garbage JSON": "{not json",
        "balance is text": json.dumps({"balance": "lots", "bets": []}),
        "bets is null": json.dumps({"balance": 100, "bets": None}),
        "negative balance": json.dumps({"balance": -500, "bets": []}),
        "bet missing fields": json.dumps({"balance": 100, "bets": [{"stake": 10}]}),
        "huge balance": json.dumps({"balance": 1e308, "bets": []}),
    }
    for label, raw in cases.items():
        pg = Page(browser, make_site(), storage=raw)
        bal = pg.js("balance")
        txt = pg.js("document.getElementById('balanceDisplay').textContent")
        pg.p.click('[data-tab="mybets"]'); pg.p.click('[data-tab="leaderboard"]')
        ok = not pg.errors and isinstance(bal, (int, float)) and bal >= 0 and "NaN" not in txt
        check(ok, f"Survives corrupt save: {label}", f"balance={bal!r} shown='{txt}' errors={pg.errors[:2]}")
        pg.close()


def t_stale_event(browser):
    """Scrapes run Mon/Thu, so an event can be over while the data still says 'upcoming'."""
    def mutate(d):
        p = d / "data" / "events.json"
        j = json.loads(p.read_text())
        j["events"][0]["date"] = (date.today() - timedelta(days=2)).isoformat()
        j["events"][0]["status"] = "upcoming"
        p.write_text(json.dumps(j))
    pg = Page(browser, make_site(mutate))
    name = pg.js("events.find(e=>e.date===%r)?.name" % (date.today() - timedelta(days=2)).isoformat())
    idx = pg.js("events.findIndex(e=>e.name===%r)" % name)
    pg.js(f"switchEvent({idx})")
    open_btns = pg.js("document.querySelectorAll('.fight-card .odd-btn:not([disabled])').length")
    check(open_btns == 0, "Can't bet on an event whose date has passed (before results are scraped)",
          f"{open_btns} bettable buttons on '{name}' dated 2 days ago")
    pg.close()


def t_payout_consistency(browser):
    """Place a pick, finish the fight, reload: credited amount must equal the slip's promise."""
    site = make_site()
    pg = Page(browser, site)
    fid = first_open_fight(pg)["id"]
    pg.js("(id)=>selectWinner(id,'A')", fid)
    pg.p.fill("#stakeInput", "333"); pg.js("updatePayout()")
    shown = int(pg.js("document.getElementById('payoutDisplay').textContent").replace(",", "").split()[0])
    pg.p.click("#placeBetBtn"); pg.p.wait_for_timeout(150)
    # which fighter/event was that? mark fighter A the winner in the data file
    a_name, ev_name = pg.js("(id)=>{for(const e of events)for(const f of e.fights)if(f.id===id)return [f.a.name,e.name]}", fid)
    for fn in ("events.json", "ufc_events.json"):
        fp = site / "data" / fn; j = json.loads(fp.read_text())
        for e in j["events"]:
            for f in e.get("fights", []):
                if f["fighter_a"]["name"] == a_name:
                    f["result"] = {"winner": "a", "method": "Decision", "round": None}
        fp.write_text(json.dumps(j))
    bal_before = pg.js("balance")
    pg.reload()
    credited = pg.js("balance") - bal_before
    check(credited == shown, "Payout credited on a win equals what the slip showed", f"slip {shown}, credited {credited}")
    check(pg.js("bets[0].result") == "won", "Settled pick shows as won", str(pg.js("bets[0]")))
    pg.close()


def t_missing_data(browser):
    def no_ufc(d): (d / "data" / "ufc_events.json").unlink()
    def broken_ufc(d): (d / "data" / "ufc_events.json").write_text("{oops")
    def all_empty(d):
        for f in ("events.json", "ufc_events.json"):
            (d / "data" / f).write_text(json.dumps({"events": []}))
    def zero_fight_event(d):
        p = d / "data" / "events.json"; j = json.loads(p.read_text())
        j["events"].insert(0, {"name": "Empty Card", "date": "2026-12-01", "status": "upcoming", "fights": []})
        p.write_text(json.dumps(j))
    def no_fights_key(d):
        p = d / "data" / "events.json"; j = json.loads(p.read_text())
        j["events"].insert(0, {"name": "Broken Card", "date": "2026-12-01", "status": "upcoming"})
        p.write_text(json.dumps(j))
    for label, fn in [("UFC file missing (404)", no_ufc), ("UFC file malformed", broken_ufc),
                      ("both files empty", all_empty), ("event with 0 fights", zero_fight_event),
                      ("event missing fights key", no_fights_key)]:
        pg = Page(browser, make_site(fn))
        errs = [e for e in pg.errors if "404" not in e and "Failed to load resource" not in e]
        has_ui = pg.js("document.getElementById('eventHeader').textContent.length>0")
        check(not errs and has_ui, f"Handles data problem: {label}", f"errors={errs[:2]} header={has_ui}")
        pg.close()


def t_hostile_names(browser):
    payload = '<img src=x onerror="window.__xss=1">'
    def mutate(d):
        p = d / "data" / "events.json"; j = json.loads(p.read_text())
        f = j["events"][0]["fights"][0]
        f["fighter_a"]["name"] = payload
        f["fighter_b"]["name"] = "Lone'er O\"Brien Ñúñez 李"
        j["events"][0]["venue"] = '<b onmouseover="window.__xss=2">Venue</b>'
        p.write_text(json.dumps(j))
    pg = Page(browser, make_site(mutate))
    pg.js("switchEvent(events.findIndex(e=>e.fights.some(f=>f.a.name.includes('img src'))))")
    pg.p.wait_for_timeout(300)
    try:
        pg.js("(()=>{const b=document.querySelector('.fight-card .odd-btn:not([disabled])'); b&&b.click();})()")
        pg.p.wait_for_timeout(200)
    except Exception:
        pass
    pg.p.click('[data-tab="mybets"]'); pg.p.wait_for_timeout(200)
    check(not pg.js("window.__xss"), "Scraped names can't inject code (XSS)",
          f"injected script ran (marker={pg.js('window.__xss')}) — data is inserted as raw HTML")
    txt = pg.js("document.body.innerText")
    check("Ñúñez" in txt and "Lone'er" in txt, "Apostrophes/accents/CJK names display", "")
    pg.close()


def t_scale(browser):
    def mutate(d):
        p = d / "data" / "events.json"; j = json.loads(p.read_text())
        base = j["events"][0]
        big = []
        for i in range(60):
            e = json.loads(json.dumps(base)); e["name"] = f"Load Test {i}"; e["date"] = f"2027-01-{(i%28)+1:02d}"
            e["fights"] = (e["fights"] * 2)[:20]
            for k, f in enumerate(e["fights"]):
                f["fighter_a"] = dict(f["fighter_a"], name=f"A{i}-{k}"); f["fighter_b"] = dict(f["fighter_b"], name=f"B{i}-{k}")
            big.append(e)
        j["events"] = big; p.write_text(json.dumps(j))
    t0 = time.time()
    pg = Page(browser, make_site(mutate))
    t = pg.js("""() => { const t=performance.now(); for(let i=0;i<events.length;i++) switchEvent(i); return performance.now()-t; }""")
    check(t < 3000 and not pg.errors, "60 events × 20 fights: tab switching stays fast", f"{t:.0f} ms total, errors={pg.errors[:1]}")
    pg.close()


def t_mobile(browser):
    for w, label in [(360, "small phone"), (390, "phone"), (768, "tablet")]:
        pg = Page(browser, make_site(), viewport=(w, 800))
        overflow = pg.js("document.documentElement.scrollWidth - window.innerWidth")
        check(overflow <= 1, f"No sideways scrolling on {label} ({w}px)", f"page is {overflow}px wider than screen")
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            pg.p.screenshot(path=str(SHOTS / f"home_{w}.png"), full_page=False)
            pg.js("(id)=>selectWinner(id,'A')", first_open_fight(pg)["id"])
            pg.p.wait_for_timeout(300)
            pg.p.screenshot(path=str(SHOTS / f"betslip_{w}.png"))
        pg.close()


def t_pages_links(browser):
    site = make_site()
    srv, url = serve(site)
    ctx = browser.new_context(); p = ctx.new_page()
    pages = ["index.html", "about.html", "contact.html", "privacy.html", "previews/index.html"] + \
            [f"previews/{f.name}" for f in sorted((site / "previews").glob("*.html")) if f.name != "index.html"]
    broken, ld_bad, errs = [], [], []
    for path in pages:
        p.on("pageerror", lambda e, path=path: errs.append(f"{path}: {e}"))
        resp = p.goto(url + path)
        if not resp or resp.status != 200:
            broken.append(f"{path} → {resp.status if resp else 'none'}"); continue
        for href in p.eval_on_selector_all("a[href]", "els=>els.map(e=>e.getAttribute('href'))"):
            if href.startswith(("http", "mailto:", "#", "javascript")):
                continue
            target = (Path(path).parent / href).as_posix()
            r = ctx.request.get(url + target)
            if r.status != 200:
                broken.append(f"{path} links to {href} → {r.status}")
        for raw in p.eval_on_selector_all('script[type="application/ld+json"]', "els=>els.map(e=>e.textContent)"):
            try:
                json.loads(raw)
            except Exception as e:
                ld_bad.append(f"{path}: {e}")
    check(not broken, "No broken internal links / pages", "; ".join(sorted(set(broken))[:6]))
    check(not ld_bad, "Structured data (JSON-LD) is valid JSON", "; ".join(ld_bad[:3]))
    check(not errs, "Content pages load without JS errors", "; ".join(errs[:3]))
    import re
    locs = re.findall(r"<loc>([^<]+)</loc>", (site / "sitemap.xml").read_text())
    base = locs[0]
    missing = [l for l in locs if ctx.request.get(url + l[len(base):]).status != 200]
    check(not missing, "Every sitemap URL exists", str(missing[:4]))
    previews = {f.name for f in (site / "previews").glob("*.html") if f.name != "index.html"}
    in_map = {l.rsplit("/", 1)[-1] for l in locs if "/previews/" in l}
    check(previews <= in_map, "Every preview page is in the sitemap", str(previews - in_map))
    ctx.close(); srv.shutdown()


def main():
    tests = [t_load_and_render, t_stakes, t_double_submit, t_topup, t_corrupt_storage, t_stale_event,
             t_payout_consistency, t_missing_data, t_hostile_names, t_scale, t_mobile, t_pages_links]
    only = [a for a in sys.argv[2:] if a.startswith("t_")]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for t in tests:
            if only and t.__name__ not in only:
                continue
            print(f"\n## {t.__name__}")
            try:
                t(browser)
            except Exception as e:
                record("FAIL", f"{t.__name__} crashed", repr(e)[:300])
        browser.close()
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print(f"\n==== {len(RESULTS)-len(fails)-len(warns)} passed, {len(fails)} failed, {len(warns)} warnings ====")
    for s, n, d in fails + warns:
        print(f"  {s}: {n} — {d}")
        if s == "FAIL":
            print(f"::error title=Site test failed::{n} — {d[:300]}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
