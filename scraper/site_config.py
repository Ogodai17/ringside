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
