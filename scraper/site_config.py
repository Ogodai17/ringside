"""
Shared site settings, read from site_config.json at the repo root.

Every page generator uses these helpers, so an ID only ever has to be
pasted in one place. Empty values switch a feature off entirely
(no fake GA/AdSense IDs loaded, no "Bet for real" button with a dead link).
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "site_config.json"

DEFAULTS = {
    "site_url": "https://ogodai17.github.io/ringside/",
    "ga_measurement_id": "",
    "adsense_publisher_id": "",
    "adsense_slot_top": "",
    "adsense_slot_bottom": "",
    "affiliate_url": "",
    "scorecards_url": "",
    "scorecards_publishable_key": "",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update({k: (v or "").strip() for k, v in json.load(f).items() if not k.startswith("_")})
    if not cfg["site_url"].endswith("/"):
        cfg["site_url"] += "/"
    return cfg


def analytics_head(cfg: dict) -> str:
    gid = cfg["ga_measurement_id"]
    if not gid:
        return ""
    gid = html.escape(gid)
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>\n'
        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
        f"gtag('js',new Date());gtag('config','{gid}');</script>"
    )


def adsense_head(cfg: dict) -> str:
    pub = cfg["adsense_publisher_id"]
    if not pub:
        return ""
    return (
        '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
        f'?client={html.escape(pub)}" crossorigin="anonymous"></script>'
    )


def ad_slot(cfg: dict, slot_key: str, wrapper_style: str = "") -> str:
    pub, slot = cfg["adsense_publisher_id"], cfg.get(slot_key, "")
    if not (pub and slot):
        return ""
    return (
        f'<div class="ad-unit" style="{wrapper_style}"><ins class="adsbygoogle" style="display:block" '
        f'data-ad-client="{html.escape(pub)}" data-ad-slot="{html.escape(slot)}" '
        'data-ad-format="auto" data-full-width-responsive="true"></ins>'
        "<script>(adsbygoogle = window.adsbygoogle || []).push({});</script></div>"
    )


def affiliate_link(cfg: dict, style: str = "") -> str:
    url = cfg["affiliate_url"]
    if not url:
        return ""
    style_attr = f' style="{style}"' if style else ""
    return (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer nofollow sponsored"'
        f"{style_attr}>&#x2197; Bet for real</a>"
    )


def social_image_tags(cfg: dict) -> str:
    """Link-preview image, icons, manifest — absolute URLs so they work from any page depth."""
    base = html.escape(cfg["site_url"])
    return (
        f'<meta property="og:image" content="{base}assets/og.png">\n'
        '<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:image" content="{base}assets/og.png">\n'
        f'<link rel="icon" type="image/png" href="{base}assets/favicon.png">\n'
        f'<link rel="apple-touch-icon" href="{base}assets/icon-180.png">\n'
        f'<link rel="manifest" href="{base}manifest.webmanifest">\n'
        '<meta name="theme-color" content="#0a0a0c">'
    )


def manifest(cfg: dict) -> str:
    return json.dumps({
        "name": "Pick A Fight — Fight Predictions",
        "short_name": "Pick A Fight",
        "description": "Free UFC & ONE Championship fight pick'em with fake money.",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#0a0a0c",
        "theme_color": "#0a0a0c",
        "icons": [
            {"src": "assets/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }, indent=2)


def score_api(cfg: dict):
    """Community scorecard database (Supabase) for the browser, or None.

    Only the PUBLISHABLE key may ever go here — it's designed to be public.
    A secret / service_role key would give every visitor full database
    access, so the build refuses to run if one is pasted by mistake.
    """
    url, key = cfg.get("scorecards_url", ""), cfg.get("scorecards_publishable_key", "")
    if not (url and key):
        return None
    looks_secret = not (key.startswith("sb_publishable_") or key.startswith("eyJ"))
    if key.startswith("eyJ"):
        import base64
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            looks_secret = json.loads(base64.urlsafe_b64decode(payload)).get("role") != "anon"
        except Exception:
            looks_secret = True
    if looks_secret:
        raise SystemExit("REFUSING TO BUILD: scorecards_publishable_key looks like a SECRET key. "
                         "Use the publishable (sb_publishable_...) or anon key only.")
    if not url.startswith("https://"):
        raise SystemExit("scorecards_url must start with https://")
    return {"url": url.rstrip("/"), "key": key}
