#!/usr/bin/env python3
"""
Build the site's static pages (index, about, contact, privacy) from
templates/ using the IDs in site_config.json.

To change the domain, Google Analytics, AdSense or the affiliate link:
edit site_config.json and commit — the GitHub workflow re-runs this.

Run locally: python scraper/build_pages.py
"""

import html
import json
from pathlib import Path

import site_config

ROOT = site_config.ROOT
TEMPLATES = ROOT / "templates"


def static_fights_html() -> str:
    """Upcoming fights as plain HTML inside #fight-cards. The app replaces it
    on load; search engines and no-JS visitors see real content + links."""
    import generate_previews as gp  # shared slug/date helpers
    events = []
    for name in ("events.json", "ufc_events.json"):
        p = ROOT / "data" / name
        if p.exists():
            try:
                events += json.loads(p.read_text(encoding="utf-8")).get("events", [])
            except Exception:
                pass
    upcoming = sorted((e for e in events if e.get("status") != "completed" and e.get("fights")),
                      key=lambda e: e.get("date") or "9999")
    if not upcoming:
        return ""
    items = []
    for e in upcoming:
        title = f"{e.get('name','Event')}: {e['subtitle']}" if e.get("subtitle") else e.get("name", "Event")
        main = e["fights"][0]
        vs = f"{main['fighter_a']['name']} vs {main['fighter_b']['name']}"
        items.append(
            f'<li><a href="previews/{gp.slugify(title)}.html">{html.escape(title)}</a> — '
            f'{html.escape(gp.fmt_date(e.get("date","")))} · {len(e["fights"])} bout{"s" if len(e["fights"]) != 1 else ""}. '
            f'Main event: <a href="previews/fights/{gp.matchup_slug(main)}.html">{html.escape(vs)} prediction</a></li>')
    return ('<div class="static-fights"><h2>Upcoming UFC &amp; ONE Championship fights</h2>'
            '<p>Free fight predictions with fake money — pick winners, methods and rounds.</p>'
            f'<ul>{"".join(items)}</ul><p><a href="previews/index.html">All fight previews &rarr;</a></p></div>')


def main():
    cfg = site_config.load()
    tokens = {
        "{{SITE_URL}}": cfg["site_url"],
        "{{GA_HEAD}}": site_config.analytics_head(cfg),
        "{{ADSENSE_HEAD}}": site_config.adsense_head(cfg),
        "{{AD_TOP}}": site_config.ad_slot(cfg, "adsense_slot_top", "text-align:center;margin:12px 0;min-height:90px;"),
        "{{AD_BOTTOM}}": site_config.ad_slot(cfg, "adsense_slot_bottom", "text-align:center;margin:16px 0;min-height:250px;"),
        # JSON-encoded so the URL is a safe JavaScript string literal
        "{{AFFILIATE_URL_JSON}}": json.dumps(cfg["affiliate_url"]),
        "{{STATIC_FIGHTS}}": static_fights_html(),
        "{{SITE_URL_JSON}}": json.dumps(cfg["site_url"]),
        "{{SOCIAL_HEAD}}": site_config.social_image_tags(cfg),
    }

    for tpl in sorted(TEMPLATES.glob("*.html")):
        page = tpl.read_text(encoding="utf-8")
        for token, value in tokens.items():
            page = page.replace(token, value)
        leftover = [t for t in tokens if t in page]
        if leftover:
            raise SystemExit(f"Unreplaced tokens in {tpl.name}: {leftover}")
        (ROOT / tpl.name).write_text(page, encoding="utf-8")
        print(f"  built {tpl.name}")

    (ROOT / "manifest.webmanifest").write_text(site_config.manifest(cfg), encoding="utf-8")

    # GitHub Pages custom domain: a CNAME file holding the bare host name
    host = cfg["site_url"].split("://", 1)[-1].split("/", 1)[0]
    cname = ROOT / "CNAME"
    if host.endswith("github.io"):
        if cname.exists():
            cname.unlink()
    else:
        cname.write_text(host + "\n", encoding="utf-8")
        print(f"  wrote CNAME ({host})")

    features = [k for k, on in [
        ("Google Analytics", cfg["ga_measurement_id"]),
        ("AdSense", cfg["adsense_publisher_id"]),
        ("affiliate button", cfg["affiliate_url"]),
    ] if on]
    print(f"✓ Pages built for {cfg['site_url']} — on: {', '.join(features) or 'none (no IDs set yet)'}")


if __name__ == "__main__":
    main()
