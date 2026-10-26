"""
Pick A Fight stress test — runs the real built site in headless Chrome.

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
    for name in ["index.html", "about.html", "contact.html", "privacy.html", "404.html", "sitemap.xml", "manifest.webmanifest"]:
        if (REPO / name).exists():
            shutil.copy(REPO / name, d / name)
    shutil.copytree(REPO / "data", d / "data")
    shutil.copytree(REPO / "previews", d / "previews")
    if (REPO / "assets").exists():
        shutil.copytree(REPO / "assets", d / "assets")
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
    for tab in ["mybets", "stats", "fights"]:
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
        pg.p.click('[data-tab="mybets"]'); pg.p.click('[data-tab="stats"]')
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
    pages = ["index.html", "about.html", "contact.html", "privacy.html", "404.html", "previews/index.html"] + \
            [f"previews/{f.name}" for f in sorted((site / "previews").glob("*.html")) if f.name != "index.html"] + \
            [f"previews/fights/{f.name}" for f in sorted((site / "previews" / "fights").glob("*.html"))]
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
    previews = {f.relative_to(site / "previews").as_posix() for f in (site / "previews").rglob("*.html") if f.name != "index.html"}
    in_map = {l.split("/previews/", 1)[1] for l in locs if "/previews/" in l}
    check(previews <= in_map, "Every preview page is in the sitemap", str(previews - in_map))
    ctx.close(); srv.shutdown()


def t_odds(browser):
    pg = Page(browser, make_site())
    bad = pg.js("""() => events.flatMap(e=>e.fights).filter(f=>!(f.oddsA>1&&f.oddsB>1&&f.oddsA<20&&f.oddsB<20)).map(f=>f.a.name+' '+f.oddsA+'/'+f.oddsB)""")
    check(not bad, "Every fight has sane odds (1.01–20)", str(bad[:4]))
    over = pg.js("() => events.flatMap(e=>e.fights).map(f=>1/f.oddsA+1/f.oddsB)")
    check(all(1.0 <= o <= 1.12 for o in over), "Odds carry a small, consistent margin (like a real line)",
          f"implied totals {min(over):.3f}–{max(over):.3f}")
    # records drive odds: the clearly better record should be the favorite
    wrong = pg.js("""() => { const r=s=>{const m=/^(\\d+)-(\\d+)/.exec(s||'');return m?[+m[1],+m[2]]:null};
        return events.flatMap(e=>e.fights).filter(f=>{const a=r(f.a.record),b=r(f.b.record); if(!a||!b) return false;
          const strong=(x,y)=>x[0]>=y[0]+8&&x[1]<=y[1]; return (strong(a,b)&&f.oddsA>f.oddsB)||(strong(b,a)&&f.oddsB>f.oddsA);})
          .map(f=>f.a.name+' '+f.a.record+' '+f.oddsA+' vs '+f.b.name+' '+f.b.record+' '+f.oddsB); }""")
    check(not wrong, "Much better record is always the favorite", str(wrong[:3]))
    pg.close()


def t_share_and_stats(browser):
    site = make_site()
    srv, url = serve(site)
    ctx = browser.new_context(viewport={"width": 390, "height": 900}, accept_downloads=True)
    p = ctx.new_page(); errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(url + "index.html"); p.wait_for_timeout(700)
    p.click(".share-btn"); p.wait_for_timeout(200)
    toast = p.evaluate("document.getElementById('toast').textContent")
    check("first" in toast.lower(), "Share with no picks explains what to do", toast)
    fid = p.evaluate("document.querySelector('.fight-card .odd-btn:not([disabled])').closest('.fight-card').id.replace('fight-','')")
    p.evaluate(f"selectWinner('{fid}','A')"); p.fill("#stakeInput", "250"); p.evaluate("updatePayout()"); p.click("#placeBetBtn")
    with p.expect_download() as dl:
        p.click(".share-btn")
    path = dl.value.path()
    size = Path(path).stat().st_size if path else 0
    check(size > 15000, "Share card image is generated", f"{size} bytes")
    p.click('[data-tab="stats"]'); p.wait_for_timeout(150)
    txt = p.evaluate("document.getElementById('stats-panel').innerText")
    check("Picks made" in txt and "1" in txt, "Stats panel shows picks", txt[:120])
    check(not p.evaluate("document.body.innerText").count("MuayThaiMax"), "No fake bot leaderboard names remain", "")
    check(not errs, "No JS errors in share/stats flow", "; ".join(errs[:2]))
    ctx.close(); srv.shutdown()


def t_static_and_meta(browser):
    site = make_site()
    html = (site / "index.html").read_text()
    import re
    static = re.search(r'<div id="fight-cards">(.*?)</div>\s*</div>', html, re.S)
    check(static and "prediction</a>" in static.group(1), "Homepage HTML contains fights for crawlers", "")
    try:
        man = json.loads((site / "manifest.webmanifest").read_text())
        icons_ok = all((site / i["src"]).exists() for i in man["icons"])
    except Exception as e:
        man, icons_ok = None, False
    check(man and icons_ok, "Web app manifest valid and icons exist", "")
    check((site / "assets" / "og.png").exists(), "Link preview image exists", "")
    srv, url = serve(site); ctx = browser.new_context(); p = ctx.new_page()
    p.goto(url + "index.html")
    og = p.evaluate("document.querySelector('meta[property=\"og:image\"]')?.content||''")
    check(og.endswith("assets/og.png"), "Homepage advertises the preview image", og)
    ctx.close(); srv.shutdown()


def _decision_fight(site):
    """Find a finished UFC decision fight in the data, return (event name, fighter a, winner side)."""
    j = json.loads((site / "data" / "ufc_events.json").read_text())
    for e in j["events"]:
        for f in e["fights"]:
            r = f.get("result") or {}
            if r.get("method") == "Decision" and r.get("winner") in ("a", "b"):
                return e["name"], e.get("subtitle", ""), f["fighter_a"]["name"], r["winner"]
    return None


def t_scorecard_solo(browser):
    site = make_site()
    found = _decision_fight(site)
    if not check(found is not None, "Test data has a finished decision fight", "none found"):
        return
    ev_name, ev_sub, a_name, winner = found
    pg = Page(browser, site, viewport=(390, 900))
    idx = pg.js("(n)=>events.findIndex(e=>e.fights.some(f=>unesc(f.a.name)===n))", a_name)
    pg.js(f"switchEvent({idx})")
    fid = pg.js("(n)=>events[activeEventIdx].fights.find(f=>unesc(f.a.name)===n).id", a_name)
    btn = f"#scw-{fid} .sc-toggle"
    check(pg.p.locator(btn).count() == 1, "Finished fights show a 'Score this fight' button", "")
    pg.p.click(btn)
    rows = pg.p.locator(f"#scw-{fid} .sc-row").count()
    check(rows in (3, 5), "Scorecard shows one row per scheduled round", f"{rows} rows")
    # Score every round for the official LOSER -> should say "Robbery?"
    loser_pick = "b109" if winner == "a" else "a109"
    for r in range(1, rows + 1):
        pg.js(f"scorePick('{fid}',{r},'{loser_pick}')")
    txt = pg.js(f"document.getElementById('scw-{fid}').innerText")
    check("Robbery?" in txt and "Official:" in txt, "Disagreeing with the judges is called out", txt[:200])
    # flip to agree
    win_pick = "a109" if winner == "a" else "b109"
    for r in range(1, rows + 1):
        pg.js(f"scorePick('{fid}',{r},'{win_pick}')")
    txt = pg.js(f"document.getElementById('scw-{fid}').innerText")
    check("agree with the judges" in txt, "Agreeing with the judges is recognised", txt[:200])
    total = pg.js(f"myTotals(events[activeEventIdx].fights.find(f=>f.id==='{fid}'), events[activeEventIdx]).text")
    check(total.startswith(f"{10*rows}-{9*rows}") or total.startswith(f"{9*rows}-{10*rows}"), "Card totals add up like a judge's", total)
    # tapping the same pick again clears it
    pg.js(f"scorePick('{fid}',1,'{win_pick}')")
    check(pg.js(f"!cards['{fid}']['1']"), "Tapping a selected score clears it", "")
    pg.reload()
    check(pg.js(f"!!cards['{fid}'] && cards['{fid}']['2']==='{win_pick}'"), "Scorecard survives a reload", "")
    pg.p.click('[data-tab="stats"]'); pg.p.wait_for_timeout(100)
    st = pg.js("document.getElementById('stats-panel').innerText")
    check("Fights you scored" in st, "My Stats counts scored fights", st[:160])
    overflow = pg.js("document.documentElement.scrollWidth - window.innerWidth")
    check(overflow <= 1, "Scorecard fits a phone screen", f"{overflow}px overflow")
    if SHOTS:
        pg.p.click('[data-tab="fights"]'); pg.js(f"switchEvent({idx})"); pg.js(f"toggleCard('{fid}')")
        pg.p.locator(f"#scw-{fid}").scroll_into_view_if_needed(); pg.p.wait_for_timeout(150)
        pg.p.screenshot(path=str(SHOTS / "scorecard_390.png"))
    check(not pg.errors, "No JS errors while scoring", "; ".join(pg.errors[:2]))
    pg.close()


def t_scorecard_one(browser):
    def mutate(d):
        p = d / "data" / "events.json"; j = json.loads(p.read_text())
        e = j["events"][0]; e["status"] = "completed"; e["date"] = "2026-01-01"
        e["fights"][0]["result"] = {"winner": "a", "method": "Decision", "round": None}
        p.write_text(json.dumps(j))
    pg = Page(browser, make_site(mutate))
    idx = pg.js("events.findIndex(e=>e.promotion==='ONE' && e.status==='completed')")
    pg.js(f"switchEvent({idx})")
    fid = pg.js("events[activeEventIdx].fights[0].id")
    pg.js(f"toggleCard('{fid}')")
    n = pg.p.locator(f"#scw-{fid} .sc-verdict .sc-btn").count()
    check(n == 6, "ONE fights get a whole-fight verdict (not rounds)", f"{n} verdict buttons")
    pg.js(f"scorePick('{fid}',0,'b3')")
    txt = pg.js(f"document.getElementById('scw-{fid}').innerText")
    check("dominant" in txt and "Robbery?" in txt, "ONE verdict compares with the official result", txt[:200])
    check(not pg.errors, "No JS errors on ONE scorecards", "; ".join(pg.errors[:2]))
    pg.close()


def t_scorecard_community(browser):
    site = make_site()
    found = _decision_fight(site)
    if not found:
        return
    _, _, a_name, winner = found
    html = (site / "index.html").read_text()
    html = html.replace("const SCORE_API = null", 'const SCORE_API = {"url":"https://fake-db.test","key":"sb_publishable_test"}')
    (site / "index.html").write_text(html)
    calls = []
    srv, url = serve(site)
    ctx = browser.new_context(viewport={"width": 390, "height": 900}); p = ctx.new_page(); errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    fid_holder = {}
    def handle(route):
        req = route.request; body = json.loads(req.post_data or "{}")
        calls.append((req.url.rsplit("/", 1)[-1], body, req.headers.get("apikey")))
        fid = fid_holder.get("id")
        if req.url.endswith("score_summary"):
            data = [{"fight_key": fid, "round": 1, "pick": "b109" if winner == "a" else "a109", "n": 14},
                    {"fight_key": fid, "round": 1, "pick": "a109" if winner == "a" else "b109", "n": 4}] if fid else []
        elif req.url.endswith("fan_verdicts"):
            loser = "b" if winner == "a" else "a"
            data = [{"fight_key": fid, "winner": loser, "n": 15}, {"fight_key": fid, "winner": winner, "n": 3}] if fid else []
        else:
            data = ""
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
    p.route("https://fake-db.test/**", handle)
    p.goto(url + "index.html"); p.wait_for_timeout(600)
    idx = p.evaluate("(n)=>events.findIndex(e=>e.fights.some(f=>unesc(f.a.name)===n))", a_name)
    fid = p.evaluate(f"(n)=>events[{idx}].fights.find(f=>unesc(f.a.name)===n).id", a_name)
    fid_holder["id"] = fid
    p.evaluate(f"switchEvent({idx})"); p.wait_for_timeout(500)
    badge = p.evaluate(f"document.querySelector('#fight-{fid} .fan-badge')?.innerText||''")
    check("robbery" in badge.lower(), "Robbery badge appears when most fans disagree with a decision", badge or "no badge")
    p.evaluate(f"toggleCard('{fid}')")
    txt = p.evaluate(f"document.getElementById('scw-{fid}').innerText")
    check("Fans:" in txt and "83%" in txt and "18 cards" in txt, "Scorecard shows fan consensus and card count", txt[:220])
    check(p.locator(f"#scw-{fid} .sc-fans").count() >= 1, "Per-round fan split bars render", "")
    p.evaluate(f"scorePick('{fid}',2,'a109')"); p.wait_for_timeout(300)
    sub = [c for c in calls if c[0] == "submit_score"]
    ok = sub and sub[-1][1].get("p_fight") == fid and sub[-1][1].get("p_round") == 2 and sub[-1][1].get("p_pick") == "a109" \
         and len(sub[-1][1].get("p_device", "")) == 36 and sub[-1][2] == "sb_publishable_test"
    check(bool(ok), "Picks are sent to the community database correctly", str(sub[-1:] if sub else "no submit call"))
    check(not errs, "No JS errors with community data", "; ".join(errs[:2]))
    ctx.close(); srv.shutdown()


def t_secret_key_guard(browser):
    sys.path.insert(0, str(REPO / "scraper"))
    import site_config
    bad = []
    for key in ["sb_secret_abc123", "random-string", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.x"]:
        try:
            site_config.score_api({"scorecards_url": "https://x.supabase.co", "scorecards_publishable_key": key})
            bad.append(key[:12])
        except SystemExit:
            pass
    ok_pub = site_config.score_api({"scorecards_url": "https://x.supabase.co", "scorecards_publishable_key": "sb_publishable_ok"})
    check(not bad and ok_pub, "Build refuses secret database keys, accepts publishable", f"accepted: {bad}")


def main():
    tests = [t_load_and_render, t_stakes, t_double_submit, t_topup, t_corrupt_storage, t_stale_event,
             t_payout_consistency, t_missing_data, t_hostile_names, t_scale, t_mobile, t_pages_links,
             t_odds, t_share_and_stats, t_static_and_meta,
             t_scorecard_solo, t_scorecard_one, t_scorecard_community, t_secret_key_guard]
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
