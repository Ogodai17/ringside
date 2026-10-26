#!/usr/bin/env python3
"""
Build the site's static pages (index, about, contact, privacy) from
templates/ using the IDs in site_config.json.

To change the domain, Google Analytics, AdSense or the affiliate link:
edit site_config.json and commit — the GitHub workflow re-runs this.

Run locally: python scraper/build_pages.py
"""

import json
from pathlib import Path

import site_config

ROOT = site_config.ROOT
TEMPLATES = ROOT / "templates"


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
    }

    for tpl in sorted(TEMPLATES.glob("*.html")):
        html = tpl.read_text(encoding="utf-8")
        for token, value in tokens.items():
            html = html.replace(token, value)
        leftover = [t for t in tokens if t in html]
        if leftover:
            raise SystemExit(f"Unreplaced tokens in {tpl.name}: {leftover}")
        (ROOT / tpl.name).write_text(html, encoding="utf-8")
        print(f"  built {tpl.name}")

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
