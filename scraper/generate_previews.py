#!/usr/bin/env python3
"""
Generate static fight preview pages from events.json.
Each event gets its own SEO-friendly HTML page with fight analysis,
odds breakdowns, and prediction context.

Run: python scraper/generate_previews.py --data data/events.json --out previews
"""

import json
import re
import sys
from pathlib import Path

# Discipline display names
DISC_LABELS = {
    "mma": "MMA",
    "mt": "Muay Thai",
    "kb": "Kickboxing",
    "grappling": "Grappling",
    "unknown": "Combat Sports",
}

# Analysis templates per discipline
ANALYSIS = {
    "mma": [
        "This {weight} MMA bout features contrasting styles. {a} ({rec_a}) brings {style_a}, while {b} ({rec_b}) looks to {style_b}.",
        "Key factors: wrestling exchanges, cage control, and cardio in later rounds.",
        "MMA's unpredictability makes this a fascinating matchup — a finish could come from any position.",
    ],
    "mt": [
        "A classic Muay Thai showdown at {weight}. {a} ({rec_a}) and {b} ({rec_b}) will battle in the art of eight limbs.",
        "Watch for clinch work, body kicks, and elbow exchanges in close quarters.",
        "Muay Thai scoring rewards aggression and clean technique — ring generalship matters here.",
    ],
    "kb": [
        "This {weight} kickboxing contest pits {a} ({rec_a}) against {b} ({rec_b}) in a striking-only affair.",
        "Hand speed, footwork, and combination punching will be decisive factors.",
        "Look for the fighter who controls distance and lands the cleaner power shots.",
    ],
    "grappling": [
        "A pure grappling match at {weight} between {a} ({rec_a}) and {b} ({rec_b}).",
        "Submission attempts, positional control, and scrambles will determine the outcome.",
        "The fighter with the more active guard game and better transitions holds the edge.",
    ],
}

STYLES_A = [
    "aggressive striking", "a well-rounded attack", "strong clinch work",
    "powerful takedowns", "slick submissions", "heavy hands",
    "relentless pressure", "technical precision", "explosive athleticism",
]
STYLES_B = [
    "counter-strike effectively", "impose a grinding pace", "utilize range and footwork",
    "work from the clinch", "find submissions from any position", "control the center",
    "overwhelm with volume", "pick shots from distance", "dictate the tempo",
]


def slugify(text: str) -> str:
    s = text.lower().strip()
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


def build_jsonld(event: dict, title_full: str, slug: str, fights: list) -> str:
    """Build JSON-LD Event schema for SEO."""
    import html as html_mod
    performers = []
    for f in fights[:5]:
        for side in ("fighter_a", "fighter_b"):
            performers.append({
                "@type": "Person",
                "name": f[side]["name"],
            })
    ld = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": html_mod.escape(title_full),
        "url": f"https://ogodai17.github.io/ringside/previews/{slug}.html",
        "sport": "Mixed Martial Arts",
        "performer": performers,
    }
    if event.get("date"):
        ld["startDate"] = event["date"]
    if event.get("venue"):
        ld["location"] = {"@type": "Place", "name": html_mod.escape(event["venue"])}
    return json.dumps(ld, ensure_ascii=False)


def build_preview_html(event: dict, idx: int) -> str:
    name = event.get("name", "Event")
    subtitle = event.get("subtitle", "")
    date = event.get("date", "")
    venue = event.get("venue", "")
    status = event.get("status", "upcoming")
    fights = event.get("fights", [])
    url = event.get("url", "")

    title_full = f"{name}: {subtitle}" if subtitle else name
    slug = slugify(title_full)
    desc = f"Fight predictions and analysis for {title_full}. Free picks for every bout — winner, method, round."
    jsonld = build_jsonld(event, title_full, slug, fights)

    fights_html = []
    for fi, fight in enumerate(fights):
        fa = fight["fighter_a"]
        fb = fight["fighter_b"]
        disc = fight.get("discipline", "mma")
        label = fight.get("label", f"Bout {fi+1}")
        disc_label = DISC_LABELS.get(disc, "MMA")
        is_main = fight.get("main_event", False)

        odds_a, odds_b = gen_odds(fa["name"], fb["name"])
        fav_name = fa["name"] if odds_a < odds_b else fb["name"]
        fav_odds = min(odds_a, odds_b)

        # Pick analysis style deterministically
        seed = hash_name(fa["name"] + fb["name"])
        style_a = STYLES_A[seed % len(STYLES_A)]
        style_b = STYLES_B[(seed >> 4) % len(STYLES_B)]

        weight = label.split("MMA")[0].split("Muay")[0].split("Kick")[0].split("Grappling")[0].strip()
        if not weight:
            weight = disc_label

        templates = ANALYSIS.get(disc, ANALYSIS["mma"])
        analysis_parts = []
        for t in templates:
            analysis_parts.append(t.format(
                a=fa["name"], b=fb["name"],
                rec_a=fa.get("record", "?") or "?",
                rec_b=fb.get("record", "?") or "?",
                weight=weight,
                style_a=style_a, style_b=style_b,
            ))

        result_html = ""
        if fight.get("result"):
            r = fight["result"]
            winner_name = fa["name"] if r["winner"] == "a" else fb["name"] if r["winner"] == "b" else "Draw"
            method = r.get("method", "")
            rnd = f" (Round {r['round']})" if r.get("round") else ""
            result_html = f"""
        <div class="result-badge">
          Result: {winner_name} wins via {method}{rnd}
        </div>"""

        main_tag = '<span class="main-event-tag">Main Event</span>' if is_main else ""

        fights_html.append(f"""
    <div class="fight-card {'fight-completed' if fight.get('result') else ''}">
      <div class="fight-header">
        <span class="disc-badge disc-{disc}">{disc_label}</span>
        {main_tag}
      </div>
      <h3>{label}</h3>
      <div class="matchup">
        <div class="fighter">
          <span class="flag">{fa.get('flag', '')}</span>
          <span class="fighter-name">{fa['name']}</span>
          <span class="record">{fa.get('record', '') or ''}</span>
          <span class="odds">{odds_a}</span>
        </div>
        <span class="vs">VS</span>
        <div class="fighter">
          <span class="flag">{fb.get('flag', '')}</span>
          <span class="fighter-name">{fb['name']}</span>
          <span class="record">{fb.get('record', '') or ''}</span>
          <span class="odds">{odds_b}</span>
        </div>
      </div>{result_html}
      <div style="text-align:center;margin:10px 0"><a href="https://www.example.com/bet?ref=ringside" target="_blank" rel="noopener noreferrer nofollow" style="display:inline-flex;align-items:center;gap:6px;border:1px solid var(--gold);border-radius:6px;padding:6px 14px;font-size:11px;font-weight:600;color:var(--gold);text-decoration:none;font-family:'Space Grotesk',sans-serif;letter-spacing:0.03em;transition:background .15s,color .15s">&#x2197; Bet for real</a></div>
      <div class="analysis">
        {'<br>'.join(f'<p>{p}</p>' for p in analysis_parts)}
        <p class="pick-note">{fav_name} is the slight favorite at {fav_odds} — <a href="../index.html">make your pick on Ringside</a>.</p>
      </div>
    </div>""")

    meta_date = f'<p class="event-date">{date}</p>' if date else ""
    meta_venue = f'<p class="event-venue">{venue}</p>' if venue else ""
    status_badge = f'<span class="status-badge status-{status}">{status.title()}</span>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-XXXXXXXXXX');</script>
<title>{title_full} — Predictions | Ringside</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{name} predictions, {name} picks, {event.get('promotion','ONE')} predictions, fight predictions, MMA picks, {', '.join(f['fighter_a']['name'] + ' vs ' + f['fighter_b']['name'] for f in fights[:3])}">
<link rel="canonical" href="https://ogodai17.github.io/ringside/previews/{slugify(title_full)}.html">
<meta property="og:type" content="article">
<meta property="og:title" content="{title_full} — Fight Predictions">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="https://ogodai17.github.io/ringside/previews/{slugify(title_full)}.html">
<meta property="og:site_name" content="Ringside">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title_full} — Fight Predictions">
<meta name="twitter:description" content="{desc}">
<script type="application/ld+json">{jsonld}</script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #0a0a0c; --surface: #141418; --surface-raised: #1c1c22;
  --border: #2a2a34; --text: #e8e6e3; --text-dim: #8a8a96;
  --text-muted: #5a5a66; --accent: #c8362e; --gold: #d4a843;
  --green: #3cb860;
}}
@media (prefers-color-scheme: light) {{
  :root:not([data-theme="dark"]) {{
    --bg: #f2f0ed; --surface: #fff; --surface-raised: #f7f6f4;
    --border: #d8d6d2; --text: #1a1a1e; --text-dim: #6a6a72;
    --text-muted: #9a9aa2; --accent: #b82e27; --gold: #b08a2e;
    --green: #2a8a45;
  }}
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Inter',system-ui,sans-serif; line-height:1.7; min-height:100vh; }}
h1,h2,h3 {{ font-family:'Space Grotesk',system-ui,sans-serif; }}
.page {{ max-width:800px; margin:0 auto; padding:0 16px; }}
.page-header {{ padding:20px 0; border-bottom:1px solid var(--border); margin-bottom:32px; }}
.page-header a {{ color:var(--accent); text-decoration:none; font-family:'Space Grotesk',system-ui,sans-serif; font-weight:700; font-size:20px; }}
h1 {{ font-size:26px; margin:0 0 8px; }}
.event-meta {{ margin-bottom:24px; }}
.event-date {{ color:var(--gold); font-size:14px; margin-bottom:4px; }}
.event-venue {{ color:var(--text-muted); font-size:13px; }}
.status-badge {{ display:inline-block; font-size:11px; font-weight:600; padding:3px 10px; border-radius:20px; text-transform:uppercase; letter-spacing:0.05em; }}
.status-upcoming {{ background:rgba(74,143,231,0.15); color:#4a8fe7; }}
.status-completed {{ background:var(--green); color:#fff; opacity:0.8; }}
.cta-banner {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; margin:24px 0; text-align:center; }}
.cta-banner a {{ color:var(--accent); font-weight:600; text-decoration:none; font-size:16px; }}
.cta-banner p {{ color:var(--text-dim); font-size:13px; margin-top:6px; }}
.fight-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:20px; }}
.fight-completed {{ opacity:0.85; }}
.fight-header {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
.disc-badge {{ font-size:11px; font-weight:600; padding:3px 8px; border-radius:4px; text-transform:uppercase; }}
.disc-mma {{ background:rgba(200,54,46,0.15); color:var(--accent); }}
.disc-mt {{ background:rgba(212,168,67,0.15); color:var(--gold); }}
.disc-kb {{ background:rgba(74,143,231,0.15); color:#4a8fe7; }}
.disc-grappling {{ background:rgba(60,184,96,0.15); color:var(--green); }}
.main-event-tag {{ font-size:10px; font-weight:700; color:var(--gold); text-transform:uppercase; letter-spacing:0.08em; }}
h3 {{ font-size:15px; margin-bottom:14px; color:var(--text-dim); font-weight:400; }}
.matchup {{ display:flex; align-items:center; justify-content:center; gap:16px; margin-bottom:14px; flex-wrap:wrap; }}
.fighter {{ display:flex; flex-direction:column; align-items:center; gap:4px; min-width:120px; }}
.fighter-name {{ font-family:'Space Grotesk',system-ui,sans-serif; font-weight:600; font-size:16px; }}
.record {{ color:var(--text-muted); font-size:12px; }}
.odds {{ color:var(--gold); font-weight:600; font-size:14px; }}
.flag {{ font-size:20px; }}
.vs {{ color:var(--text-muted); font-weight:700; font-size:12px; letter-spacing:0.1em; }}
.result-badge {{ background:var(--gold); color:#000; font-weight:600; font-size:13px; padding:6px 14px; border-radius:6px; text-align:center; margin-bottom:12px; }}
.analysis {{ border-top:1px solid var(--border); padding-top:14px; }}
.analysis p {{ color:var(--text-dim); font-size:14px; margin-bottom:10px; }}
.pick-note {{ font-style:italic; color:var(--text-muted); font-size:13px; }}
.pick-note a {{ color:var(--accent); }}
.footer {{ border-top:1px solid var(--border); margin-top:48px; padding:24px 0; text-align:center; color:var(--text-muted); font-size:13px; }}
.footer a {{ color:var(--accent); text-decoration:none; }}
</style>
</head>
<body>
<div class="page">
  <div class="page-header"><a href="../index.html">Ringside</a></div>

  <h1>{title_full}</h1>
  <div class="event-meta">
    {meta_date}
    {meta_venue}
    {status_badge}
  </div>

  <div class="cta-banner">
    <a href="../index.html">Make Your Predictions on Ringside &rarr;</a>
    <p>Free to play — pick winners, methods, and rounds with fake money.</p>
  </div>

  <h2 style="font-size:20px;margin-bottom:20px;">{len(fights)} Bout{'s' if len(fights) != 1 else ''} — Full Card Breakdown</h2>

  {''.join(fights_html)}

  <div class="cta-banner">
    <a href="../index.html">Ready to make your picks? Play on Ringside &rarr;</a>
    <p>5,000 R starting balance. No real money. Just bragging rights.</p>
  </div>

  <div class="footer">
    <a href="../index.html">Ringside</a> · <a href="../about.html">About</a> · <a href="../privacy.html">Privacy</a> · <a href="../contact.html">Contact</a>
  </div>
</div>
</body>
</html>"""


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/events.json")
    parser.add_argument("--out", default="previews")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"No data file at {data_path}", file=sys.stderr)
        sys.exit(1)

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    events = data.get("events", [])
    print(f"Generating previews for {len(events)} events...")

    # Also build an index page linking all previews
    index_links = []

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
        f'<span class="preview-title">{l["title"]}</span>'
        f'<span class="preview-meta">{l["fights"]} bouts · {l["status"].title()}'
        f'{" · " + l["date"] if l["date"] else ""}</span></a>'
        for l in index_links
    )

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-XXXXXXXXXX');</script>
<title>Fight Previews — Ringside</title>
<meta name="description" content="Fight predictions and analysis for upcoming UFC and ONE Championship events. Free picks and breakdowns for every bout.">
<link rel="canonical" href="https://ogodai17.github.io/ringside/previews/">
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
  <div class="page-header"><a href="../index.html">Ringside</a></div>

  <h1>Fight Previews</h1>
  <p class="tagline">Analysis and predictions for every bout.</p>

  <div class="preview-list">
{links_html}
  </div>

  <div class="footer">
    <a href="../index.html">Ringside</a> · <a href="../about.html">About</a> · <a href="../privacy.html">Privacy</a> · <a href="../contact.html">Contact</a>
  </div>
</div>
</body>
</html>"""

    with open(out_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

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
    base = "https://ogodai17.github.io/ringside/"
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
    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(sitemap_urls) + "\n</urlset>\n"
    )
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(sitemap_xml)
    print(f"✓ Updated sitemap.xml with {len(sitemap_urls)} URLs")

    print(f"\n✓ Generated {len(index_links)} preview pages + index in {out_dir}/")


if __name__ == "__main__":
    main()
