#!/usr/bin/env python3
"""
Generate static fight preview pages from events.json.
Each event gets its own SEO-friendly HTML page with fight analysis,
odds breakdowns, and prediction context.

Run: python scraper/generate_previews.py --data data/events.json --out previews
"""

import html as html_mod
import json
import re
import sys
from pathlib import Path

import site_config

CFG = site_config.load()
BASE = CFG["site_url"]
GA_HEAD = site_config.analytics_head(CFG)
SOCIAL_IMAGE = site_config.social_image_tags(CFG)

# Discipline display names
DISC_LABELS = {
    "mma": "MMA",
    "mt": "Muay Thai",
    "kb": "Kickboxing",
    "grappling": "Grappling",
    "unknown": "Combat Sports",
}

AFF_STYLE = "display:inline-flex;align-items:center;gap:6px;border:1px solid var(--gold);border-radius:6px;padding:6px 14px;font-size:11px;font-weight:600;color:var(--gold);text-decoration:none;font-family:'Space Grotesk',sans-serif;letter-spacing:0.03em;transition:background .15s,color .15s"
AFF_HTML = (
    f'<div style="text-align:center;margin:10px 0">{site_config.affiliate_link(CFG, AFF_STYLE)}</div>'
    if CFG["affiliate_url"] else ""
)


PAGE_CSS = """<style>
:root {
  --bg: #0a0a0c; --surface: #141418; --surface-raised: #1c1c22;
  --border: #2a2a34; --text: #e8e6e3; --text-dim: #8a8a96;
  --text-muted: #5a5a66; --accent: #c8362e; --gold: #d4a843;
  --green: #3cb860;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #f2f0ed; --surface: #fff; --surface-raised: #f7f6f4;
    --border: #d8d6d2; --text: #1a1a1e; --text-dim: #6a6a72;
    --text-muted: #9a9aa2; --accent: #b82e27; --gold: #b08a2e;
    --green: #2a8a45;
  }
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:'Inter',system-ui,sans-serif; line-height:1.7; min-height:100vh; }
h1,h2,h3 { font-family:'Space Grotesk',system-ui,sans-serif; }
.page { max-width:800px; margin:0 auto; padding:0 16px; }
.page-header { padding:20px 0; border-bottom:1px solid var(--border); margin-bottom:32px; }
.page-header a { color:var(--accent); text-decoration:none; font-family:'Space Grotesk',system-ui,sans-serif; font-weight:700; font-size:20px; }
h1 { font-size:26px; margin:0 0 8px; }
.event-meta { margin-bottom:24px; }
.event-date { color:var(--gold); font-size:14px; margin-bottom:4px; }
.event-venue { color:var(--text-muted); font-size:13px; }
.status-badge { display:inline-block; font-size:11px; font-weight:600; padding:3px 10px; border-radius:20px; text-transform:uppercase; letter-spacing:0.05em; }
.status-upcoming { background:rgba(74,143,231,0.15); color:#4a8fe7; }
.status-completed { background:var(--green); color:#fff; opacity:0.8; }
.cta-banner { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; margin:24px 0; text-align:center; }
.cta-banner a { color:var(--accent); font-weight:600; text-decoration:none; font-size:16px; }
.cta-banner p { color:var(--text-dim); font-size:13px; margin-top:6px; }
.fight-card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:20px; }
.fight-completed { opacity:0.85; }
.fight-header { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
.disc-badge { font-size:11px; font-weight:600; padding:3px 8px; border-radius:4px; text-transform:uppercase; }
.disc-mma { background:rgba(200,54,46,0.15); color:var(--accent); }
.disc-mt { background:rgba(212,168,67,0.15); color:var(--gold); }
.disc-kb { background:rgba(74,143,231,0.15); color:#4a8fe7; }
.disc-grappling { background:rgba(60,184,96,0.15); color:var(--green); }
.main-event-tag { font-size:10px; font-weight:700; color:var(--gold); text-transform:uppercase; letter-spacing:0.08em; }
h3 { font-size:15px; margin-bottom:14px; color:var(--text-dim); font-weight:400; }
.matchup { display:flex; align-items:center; justify-content:center; gap:16px; margin-bottom:14px; flex-wrap:wrap; }
.fighter { display:flex; flex-direction:column; align-items:center; gap:4px; min-width:120px; }
.fighter-name { font-family:'Space Grotesk',system-ui,sans-serif; font-weight:600; font-size:16px; }
.record { color:var(--text-muted); font-size:12px; }
.odds { color:var(--gold); font-weight:600; font-size:14px; }
.flag { font-size:20px; }
.vs { color:var(--text-muted); font-weight:700; font-size:12px; letter-spacing:0.1em; }
.result-badge { background:var(--gold); color:#000; font-weight:600; font-size:13px; padding:6px 14px; border-radius:6px; text-align:center; margin-bottom:12px; }
.analysis { border-top:1px solid var(--border); padding-top:14px; }
.analysis p { color:var(--text-dim); font-size:14px; margin-bottom:10px; }
.pick-note { font-style:italic; color:var(--text-muted); font-size:13px; }
.pick-note a { color:var(--accent); }
.footer { border-top:1px solid var(--border); margin-top:48px; padding:24px 0; text-align:center; color:var(--text-muted); font-size:13px; }
.footer a { color:var(--accent); text-decoration:none; }
</style>
<style>
.stat-table { width:100%; border-collapse:collapse; margin:8px 0 16px; font-size:14px; }
.stat-table th, .stat-table td { padding:8px 6px; border-bottom:1px solid var(--border); text-align:center; }
.stat-table th { color:var(--text-muted); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
.stat-table td:first-child, .stat-table th:first-child { text-align:left; color:var(--text-dim); }
.prob-bar { display:flex; height:10px; border-radius:6px; overflow:hidden; margin:6px 0 4px; background:var(--surface-raised); }
.prob-a { background:var(--accent); } .prob-b { background:var(--gold); }
.prob-labels { display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted); }
.more-link { display:inline-block; margin-top:6px; font-size:13px; color:var(--accent); text-decoration:none; font-weight:600; }
.related a { display:block; padding:8px 0; color:var(--text); text-decoration:none; border-bottom:1px solid var(--border); font-size:14px; }
</style>"""


def h(v) -> str:
    """Escape scraped text for HTML."""
    return html_mod.escape(str(v if v is not None else ""), quote=True)


def safe_jsonld(obj) -> str:
    """JSON for a <script> tag: raw values (no HTML entities), but '<' escaped."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


def parse_rec(rec: str):
    m = re.match(r"^\s*(\d+)-(\d+)(?:-(\d+))?", rec or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def fight_odds(fight: dict):
    o = fight.get("odds")
    if isinstance(o, list) and len(o) == 2 and all(isinstance(x, (int, float)) and x > 1 for x in o):
        return o[0], o[1]
    return gen_odds(fight["fighter_a"]["name"], fight["fighter_b"]["name"])


def win_chance(fight: dict) -> float:
    p = fight.get("p_a")
    if isinstance(p, (int, float)) and 0 < p < 1:
        return float(p)
    oa, ob = fight_odds(fight)
    return (1 / oa) / ((1 / oa) + (1 / ob))


def favorite_phrase(p: float) -> str:
    edge = max(p, 1 - p)
    if edge < 0.55:
        return "a near coin flip"
    if edge < 0.65:
        return "a slight favorite"
    if edge < 0.78:
        return "a clear favorite"
    return "a heavy favorite"


def analysis_paragraphs(fight: dict) -> list[str]:
    """Fact-based analysis from the data we actually have — no invented
    fighting styles. Returns already-escaped HTML paragraphs."""
    fa, fb = fight["fighter_a"], fight["fighter_b"]
    na, nb = h(fa["name"]), h(fb["name"])
    ra, rb = parse_rec(fa.get("record", "")), parse_rec(fb.get("record", ""))
    out = []

    def describe(name, r):
        if not r:
            return f"{name}'s pro record wasn't available"
        w, l, d = r
        n = w + l + d
        if l == 0 and w > 0:
            return f"{name} is unbeaten at {w}-{l}" + (f"-{d}" if d else "") + f" across {n} pro fights"
        pct = round(100 * w / n) if n else 0
        return f"{name} is {w}-{l}" + (f"-{d}" if d else "") + f", winning {pct}% of {n} pro fights"

    out.append(f"{describe(na, ra)}; {describe(nb, rb)}.")
    if ra and rb:
        gap = sum(ra) - sum(rb)
        if abs(gap) >= 8:
            more, less = (na, nb) if gap > 0 else (nb, na)
            out.append(f"Experience is a real gap here: {more} has {abs(gap)} more pro fights than {less}.")
    fr = [(n_, f_.get("finish_rate")) for n_, f_ in ((na, fa), (nb, fb)) if isinstance(f_.get("finish_rate"), int)]
    if fr:
        out.append(" ".join(f"{n_} finishes {r_}% of their wins." for n_, r_ in fr))

    p = win_chance(fight)
    oa, ob = fight_odds(fight)
    fav, fav_p, fav_o = (na, p, oa) if p >= 0.5 else (nb, 1 - p, ob)
    if not (ra or rb):
        out.append("With no records on file for either fighter, Pick A Fight prices this as close to even.")
    else:
        phrase = favorite_phrase(p)
        lead = (f"Pick A Fight's record-based model calls this {phrase}, leaning {fav}"
                if phrase == "a near coin flip" else
                f"Pick A Fight's record-based model makes {fav} {phrase}")
        out.append(f"{lead} ({round(fav_p * 100)}% to win, odds {fav_o:.2f}). It only looks at records — "
                   f"opposition quality, styles, layoffs and weight cuts aren't in it, which is where upsets come from.")
    return out


def prob_bar(fight: dict) -> str:
    p = win_chance(fight)
    a, b = round(p * 100), 100 - round(p * 100)
    na, nb = h(fight["fighter_a"]["name"]), h(fight["fighter_b"]["name"])
    return (f'<div class="prob-bar" role="img" aria-label="{na} {a}% vs {nb} {b}%">'
            f'<div class="prob-a" style="width:{a}%"></div><div class="prob-b" style="width:{b}%"></div></div>'
            f'<div class="prob-labels"><span>{na} {a}%</span><span>{nb} {b}%</span></div>')


def matchup_slug(fight: dict) -> str:
    return slugify(f"{fight['fighter_a']['name']} vs {fight['fighter_b']['name']} prediction")


def featured_fights(event: dict) -> list[dict]:
    """Main + co-main of upcoming events get their own matchup page."""
    if event.get("status") == "completed":
        return []
    return [f for f in event.get("fights", [])[:2] if f.get("fighter_a", {}).get("name") and f.get("fighter_b", {}).get("name")]


def slugify(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")  # Procházka -> Prochazka
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def hash_name(s: str) -> int:
    h = 0
    for c in s:
        h = ((h << 5) - h) + ord(c)
        h &= 0xFFFFFFFF
    return h


def gen_odds(a: str, b: str):
    seed = (hash_name(a) ^ hash_name(b)) % 100
    fav = 1.20 + (seed % 40) / 100
    dog = round(4.5 - fav * 1.05, 2)
    if seed % 2 == 0:
        return round(fav, 2), dog
    return dog, round(fav, 2)


def fmt_date(d: str) -> str:
    try:
        from datetime import date as _d
        return _d.fromisoformat(d).strftime("%a, %b %-d, %Y")
    except Exception:
        return d or ""


def build_jsonld(event: dict, title_full: str, url: str, fights: list) -> str:
    """JSON-LD SportsEvent for search engines (raw values, script-safe)."""
    ld = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": title_full,
        "url": url,
        "sport": "Mixed Martial Arts",
        "performer": [{"@type": "Person", "name": f[s_]["name"]}
                      for f in fights[:5] for s_ in ("fighter_a", "fighter_b")],
    }
    if event.get("start") or event.get("date"):
        ld["startDate"] = event.get("start") or event["date"]
    if event.get("venue"):
        ld["location"] = {"@type": "Place", "name": event["venue"]}
    return safe_jsonld(ld)


def page_head(title: str, desc: str, url: str, jsonld: str, og_type: str = "article") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{GA_HEAD}
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="Pick A Fight">
{SOCIAL_IMAGE}
<script type="application/ld+json">{jsonld}</script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
{PAGE_CSS}
</head>"""


def page_footer(prefix: str) -> str:
    return f"""
  <div class="footer">
    <a href="{prefix}index.html">Pick A Fight</a> · <a href="{prefix}previews/index.html">All previews</a> · <a href="{prefix}about.html">About</a> · <a href="{prefix}privacy.html">Privacy</a> · <a href="{prefix}contact.html">Contact</a>
  </div>
</div>
</body>
</html>"""


def fighter_block(f: dict, odds: float) -> str:
    return f"""<div class="fighter">
          <span class="flag">{h(f.get('flag', ''))}</span>
          <span class="fighter-name">{h(f['name'])}</span>
          <span class="record">{h(f.get('record', '') or 'record n/a')}</span>
          <span class="odds">{odds:.2f}</span>
        </div>"""


def result_line(fight: dict) -> str:
    r = fight.get("result")
    if not r:
        return ""
    fa, fb = fight["fighter_a"], fight["fighter_b"]
    who = h(fa["name"]) if r.get("winner") == "a" else h(fb["name"]) if r.get("winner") == "b" else "Draw"
    how = h(r.get("method") or "finish")
    rnd = f" (Round {int(r['round'])})" if r.get("round") else ""
    return f'\n      <div class="result-badge">Result: {who} wins via {how}{rnd}</div>'


def build_preview_html(event: dict, idx: int) -> str:
    name, subtitle = event.get("name", "Event"), event.get("subtitle", "")
    status = "completed" if event.get("status") == "completed" else "upcoming"
    fights = event.get("fights", [])
    title_full = f"{name}: {subtitle}" if subtitle else name
    slug = slugify(title_full)
    url = f"{BASE}previews/{slug}.html"
    desc = h(f"{title_full} predictions: odds, records and a breakdown for all {len(fights)} bouts. Free picks — winner, method, round.")
    jsonld = build_jsonld(event, title_full, url, fights)
    featured = {id(f) for f in featured_fights(event)}

    cards = []
    for fi, fight in enumerate(fights):
        fa, fb = fight["fighter_a"], fight["fighter_b"]
        disc = fight.get("discipline", "mma")
        disc = disc if disc in DISC_LABELS else "mma"
        oa, ob = fight_odds(fight)
        main_tag = '<span class="main-event-tag">Main Event</span>' if fight.get("main_event") else ""
        more = (f'<a class="more-link" href="fights/{matchup_slug(fight)}.html">Full {h(fa["name"])} vs {h(fb["name"])} breakdown &rarr;</a>'
                if id(fight) in featured else "")
        cards.append(f"""
    <div class="fight-card {'fight-completed' if fight.get('result') else ''}">
      <div class="fight-header">
        <span class="disc-badge disc-{disc}">{DISC_LABELS[disc]}</span>
        {main_tag}
      </div>
      <h3>{h(fight.get('label') or f'Bout {fi+1}')}</h3>
      <div class="matchup">
        {fighter_block(fa, oa)}
        <span class="vs">VS</span>
        {fighter_block(fb, ob)}
      </div>{result_line(fight)}
      {prob_bar(fight)}
      {AFF_HTML}
      <div class="analysis">
        {''.join(f'<p>{p_}</p>' for p_ in analysis_paragraphs(fight))}
        {more}
      </div>
    </div>""")

    meta_date = f'<p class="event-date">{h(fmt_date(event.get("date", "")))}</p>' if event.get("date") else ""
    meta_venue = f'<p class="event-venue">{h(event.get("venue", ""))}</p>' if event.get("venue") else ""
    return page_head(f"{h(title_full)} Predictions &amp; Odds | Pick A Fight", desc, url, jsonld) + f"""
<body>
<div class="page">
  <div class="page-header"><a href="../index.html">Pick A Fight</a></div>

  <h1>{h(title_full)}</h1>
  <div class="event-meta">
    {meta_date}
    {meta_venue}
    <span class="status-badge status-{status}">{status.title()}</span>
  </div>

  <div class="cta-banner">
    <a href="../index.html">Make Your Predictions on Pick A Fight &rarr;</a>
    <p>Free to play — pick winners, methods, and rounds with fake money.</p>
  </div>

  <h2 style="font-size:20px;margin-bottom:20px;">{len(fights)} Bout{'s' if len(fights) != 1 else ''} — Full Card Breakdown</h2>
  {''.join(cards)}

  <div class="cta-banner">
    <a href="../index.html">Ready to make your picks? Play on Pick A Fight &rarr;</a>
    <p>5,000 R starting balance. No real money. Just bragging rights.</p>
  </div>
""" + page_footer("../")


def build_matchup_html(event: dict, fight: dict, event_file: str) -> str:
    """Dedicated '<A> vs <B> prediction' page — what people search fight week."""
    fa, fb = fight["fighter_a"], fight["fighter_b"]
    name, subtitle = event.get("name", "Event"), event.get("subtitle", "")
    ev_title = f"{name}: {subtitle}" if subtitle else name
    vs = f"{fa['name']} vs {fb['name']}"
    url = f"{BASE}previews/fights/{matchup_slug(fight)}.html"
    oa, ob = fight_odds(fight)
    p = win_chance(fight)
    when = fmt_date(event.get("date", ""))
    desc = h(f"{vs} prediction and odds for {ev_title}" + (f" ({when})" if when else "") +
             f". Records, win chances and a free pick'em on Pick A Fight.")
    ld = {
        "@context": "https://schema.org", "@type": "SportsEvent",
        "name": f"{vs} — {ev_title}", "url": url, "sport": "Mixed Martial Arts",
        "competitor": [{"@type": "Person", "name": fa["name"]}, {"@type": "Person", "name": fb["name"]}],
        "superEvent": {"@type": "SportsEvent", "name": ev_title, "url": f"{BASE}previews/{event_file}"},
    }
    if event.get("start") or event.get("date"):
        ld["startDate"] = event.get("start") or event["date"]
    if event.get("venue"):
        ld["location"] = {"@type": "Place", "name": event["venue"]}

    def row(label, a, b):
        return f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>"
    ra, rb = parse_rec(fa.get("record", "")), parse_rec(fb.get("record", ""))
    def pct(r): return f"{round(100 * r[0] / sum(r))}%" if r and sum(r) else "—"
    rows = [
        row("Record", h(fa.get("record") or "—"), h(fb.get("record") or "—")),
        row("Win rate", pct(ra), pct(rb)),
        row("Pro fights", sum(ra) if ra else "—", sum(rb) if rb else "—"),
        row("Country", h(fa.get("country") or "—"), h(fb.get("country") or "—")),
        row("Pick A Fight odds", f"{oa:.2f}", f"{ob:.2f}"),
        row("Win chance", f"{round(p * 100)}%", f"{100 - round(p * 100)}%"),
    ]
    if any(isinstance(x.get("finish_rate"), int) for x in (fa, fb)):
        rows.insert(3, row("Finish rate", f"{fa['finish_rate']}%" if isinstance(fa.get("finish_rate"), int) else "—",
                           f"{fb['finish_rate']}%" if isinstance(fb.get("finish_rate"), int) else "—"))
    others = [f for f in event.get("fights", []) if f is not fight][:6]
    related = "".join(f'<a href="../{event_file}">{h(o["fighter_a"]["name"])} vs {h(o["fighter_b"]["name"])} <span style="color:var(--text-muted)">· {h(o.get("label",""))}</span></a>' for o in others)
    tag = "Main Event" if fight.get("main_event") else "Co-Main Event"

    return page_head(f"{h(vs)} Prediction &amp; Odds | Pick A Fight", desc, url, safe_jsonld(ld)) + f"""
<body>
<div class="page">
  <div class="page-header"><a href="../../index.html">Pick A Fight</a></div>

  <h1>{h(vs)} Prediction</h1>
  <div class="event-meta">
    <p class="event-date">{tag} · <a href="../{event_file}" style="color:inherit">{h(ev_title)}</a></p>
    <p class="event-venue">{h(when)}{' · ' + h(event.get('venue')) if event.get('venue') else ''}</p>
  </div>

  <div class="fight-card">
    <h3>{h(fight.get('label', ''))}</h3>
    <div class="matchup">
      {fighter_block(fa, oa)}
      <span class="vs">VS</span>
      {fighter_block(fb, ob)}
    </div>{result_line(fight)}
    {prob_bar(fight)}
    <table class="stat-table"><thead><tr><th></th><th>{h(fa['name'])}</th><th>{h(fb['name'])}</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    <div class="analysis">
      {''.join(f'<p>{p_}</p>' for p_ in analysis_paragraphs(fight))}
    </div>
    {AFF_HTML}
  </div>

  <div class="cta-banner">
    <a href="../../index.html">Make your {h(vs)} pick on Pick A Fight &rarr;</a>
    <p>Free pick'em with fake money — winner, method, round. No sign-up.</p>
  </div>

  <h2 style="font-size:18px;margin:28px 0 8px;">More from {h(ev_title)}</h2>
  <div class="related">{related}</div>
""" + page_footer("../../")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    # Pass ALL data files in one run. (Running once per promotion made the
    # second run overwrite the sitemap + preview index with only its own events.)
    parser.add_argument("--data", nargs="+", default=["data/events.json", "data/ufc_events.json"])
    parser.add_argument("--out", default="previews")
    args = parser.parse_args()

    events = []
    for data_file in args.data:
        data_path = Path(data_file)
        if not data_path.exists():
            print(f"  (skipping missing {data_path})", file=sys.stderr)
            continue
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        for ev in data.get("events", []):
            ev.setdefault("promotion", data.get("promotion", "ONE"))
            events.append(ev)

    # Upcoming first (soonest first), then completed (most recent first)
    upcoming = sorted([e for e in events if e.get("status") != "completed"], key=lambda e: e.get("date") or "9999")
    done = sorted([e for e in events if e.get("status") == "completed"], key=lambda e: e.get("date") or "", reverse=True)
    events = upcoming + done

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not events:
        print("No events in any data file — leaving existing previews alone.", file=sys.stderr)
        sys.exit(1)

    print(f"Generating previews for {len(events)} events...")

    # Also build an index page linking all previews
    index_links = []
    matchup_links = []

    for i, event in enumerate(events):
        name = event.get("name", "Event")
        subtitle = event.get("subtitle", "")
        title_full = f"{name}: {subtitle}" if subtitle else name
        slug = slugify(title_full)
        filename = f"{slug}.html"

        html = build_preview_html(event, i)
        out_file = out_dir / filename
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html)

        fight_count = len(event.get("fights", []))
        status = event.get("status", "upcoming")
        # Main / co-main matchup pages for upcoming events
        fights_dir = out_dir / "fights"
        fights_dir.mkdir(exist_ok=True)
        for fight in featured_fights(event):
            mslug = matchup_slug(fight)
            (fights_dir / f"{mslug}.html").write_text(build_matchup_html(event, fight, filename), encoding="utf-8")
            matchup_links.append({"filename": f"fights/{mslug}.html",
                                  "title": f"{fight['fighter_a']['name']} vs {fight['fighter_b']['name']}",
                                  "event": title_full, "date": event.get("date", "")})
        index_links.append({
            "title": title_full,
            "filename": filename,
            "fights": fight_count,
            "status": status,
            "date": event.get("date", ""),
        })
        print(f"  -> {filename} ({fight_count} fights)")

    # Generate preview index
    links_html = "\n".join(
        f'    <a href="{l["filename"]}" class="preview-link">'
        f'<span class="preview-title">{h(l["title"])}</span>'
        f'<span class="preview-meta">{l["fights"]} bouts · {l["status"].title()}'
        f'{" · " + h(fmt_date(l["date"])) if l["date"] else ""}</span></a>'
        for l in index_links
    )
    if matchup_links:
        links_html = ('  <h2 style="font-size:18px;margin:8px 0 12px;">Featured matchups</h2>\n' + "\n".join(
            f'    <a href="{m["filename"]}" class="preview-link"><span class="preview-title">{h(m["title"])} prediction</span>'
            f'<span class="preview-meta">{h(m["event"])}{" · " + h(fmt_date(m["date"])) if m["date"] else ""}</span></a>'
            for m in matchup_links) + '\n  <h2 style="font-size:18px;margin:28px 0 12px;">All events</h2>\n' + links_html)

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{GA_HEAD}
{SOCIAL_IMAGE}
<title>Fight Previews — Pick A Fight</title>
<meta name="description" content="Fight predictions and analysis for upcoming UFC and ONE Championship events. Free picks and breakdowns for every bout.">
<link rel="canonical" href="{BASE}previews/">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #0a0a0c; --surface: #141418; --surface-raised: #1c1c22;
  --border: #2a2a34; --text: #e8e6e3; --text-dim: #8a8a96;
  --text-muted: #5a5a66; --accent: #c8362e; --gold: #d4a843;
}}
@media (prefers-color-scheme: light) {{
  :root:not([data-theme="dark"]) {{
    --bg: #f2f0ed; --surface: #fff; --surface-raised: #f7f6f4;
    --border: #d8d6d2; --text: #1a1a1e; --text-dim: #6a6a72;
    --text-muted: #9a9aa2; --accent: #b82e27; --gold: #b08a2e;
  }}
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Inter',system-ui,sans-serif; line-height:1.7; }}
h1 {{ font-family:'Space Grotesk',system-ui,sans-serif; }}
.page {{ max-width:720px; margin:0 auto; padding:0 16px; }}
.page-header {{ padding:20px 0; border-bottom:1px solid var(--border); margin-bottom:32px; }}
.page-header a {{ color:var(--accent); text-decoration:none; font-family:'Space Grotesk',system-ui,sans-serif; font-weight:700; font-size:20px; }}
h1 {{ font-size:28px; margin:0 0 8px; }}
.tagline {{ color:var(--gold); font-size:16px; margin-bottom:32px; font-family:'Space Grotesk',system-ui,sans-serif; }}
.preview-link {{ display:block; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px 20px; margin-bottom:12px; text-decoration:none; transition:border-color 0.15s; }}
.preview-link:hover {{ border-color:var(--accent); }}
.preview-title {{ display:block; font-family:'Space Grotesk',system-ui,sans-serif; font-weight:600; font-size:16px; color:var(--text); margin-bottom:4px; }}
.preview-meta {{ display:block; font-size:13px; color:var(--text-muted); }}
.footer {{ border-top:1px solid var(--border); margin-top:48px; padding:24px 0; text-align:center; color:var(--text-muted); font-size:13px; }}
.footer a {{ color:var(--accent); text-decoration:none; }}
</style>
</head>
<body>
<div class="page">
  <div class="page-header"><a href="../index.html">Pick A Fight</a></div>

  <h1>Fight Previews</h1>
  <p class="tagline">Analysis and predictions for every bout.</p>

  <div class="preview-list">
{links_html}
  </div>

  <div class="footer">
    <a href="../index.html">Pick A Fight</a> · <a href="../about.html">About</a> · <a href="../privacy.html">Privacy</a> · <a href="../contact.html">Contact</a>
  </div>
</div>
</body>
</html>"""

    with open(out_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    # Remove preview pages for events/matchups no longer in the data
    keep = {l["filename"] for l in index_links} | {"index.html"} | {m["filename"] for m in matchup_links}
    for old in list(out_dir.glob("*.html")) + list((out_dir / "fights").glob("*.html")):
        rel = old.relative_to(out_dir).as_posix()
        if rel not in keep:
            old.unlink()
            print(f"  removed stale {rel}")

    # Regenerate sitemap.xml with all preview URLs
    sitemap_path = out_dir.parent / "sitemap.xml"
    static_pages = [
        ("", "daily", "1.0"),
        ("about.html", "monthly", "0.6"),
        ("privacy.html", "monthly", "0.4"),
        ("contact.html", "monthly", "0.5"),
        ("previews/index.html", "weekly", "0.8"),
    ]
    sitemap_urls = []
    base = BASE
    for path, freq, pri in static_pages:
        sitemap_urls.append(
            f"  <url>\n    <loc>{base}{path}</loc>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{pri}</priority>\n  </url>"
        )
    for link in index_links:
        sitemap_urls.append(
            f"  <url>\n    <loc>{base}previews/{link['filename']}</loc>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>0.7</priority>\n  </url>"
        )
    for m in matchup_links:
        sitemap_urls.append(
            f"  <url>\n    <loc>{base}previews/{m['filename']}</loc>\n"
            f"    <changefreq>daily</changefreq>\n"
            f"    <priority>0.8</priority>\n  </url>"
        )
    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(sitemap_urls) + "\n</urlset>\n"
    )
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(sitemap_xml)
    print(f"✓ Updated sitemap.xml with {len(sitemap_urls)} URLs")

    print(f"\n✓ Generated {len(index_links)} event pages, {len(matchup_links)} matchup pages + index in {out_dir}/")


if __name__ == "__main__":
    main()
