"""Translations for the menu, notifications, and CLI. All text lives in `locales/<lang>.json`;
code refers to keys only.

Language resolution: `PORTLESS_MANAGER_LANG` → `language` in config.json → the macOS preferred
language (`AppleLanguages`) → `LC_ALL`/`LANG` → `en`. GUI apps such as SwiftBar often don't pass
`LANG`, so the macOS setting is checked first.

A key missing from a translation falls back to `en.json`, then to the key itself, so the menu
never breaks.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

LOCALES = Path(__file__).with_name("locales")
FALLBACK = "en"
_forced: str | None = None


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    try:
        d = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str) and not k.startswith("_")}


def available() -> list[str]:
    return sorted(p.stem for p in LOCALES.glob("*.json"))


def match(tag: str | None) -> str | None:
    """Map a tag like `ko-KR`, `ko_KR.UTF-8`, or `zh-Hans` to an existing locale file (full tag, then base language)."""
    if not tag:
        return None
    tag = tag.split(".")[0].replace("_", "-").strip().lower()
    if not tag or tag in ("c", "posix"):
        return None
    have = {l.lower(): l for l in available()}
    for cand in (tag, tag.split("-")[0]):
        if cand in have:
            return have[cand]
    return None


def parse_apple_languages(out: str) -> str | None:
    m = re.search(r'"?([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)"?', out.replace("(", " ").replace(")", " "))
    return m.group(1) if m else None


def _macos_language() -> str | None:
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_apple_languages(out)


def _config_language() -> str | None:
    from .discover import CONFIG, _read_json
    cfg = _read_json(CONFIG)
    v = cfg.get("language") if cfg else None
    return v if isinstance(v, str) and v != "auto" else None


@lru_cache(maxsize=1)
def _detect() -> str:
    for source in (lambda: os.environ.get("PORTLESS_MANAGER_LANG"), _config_language, _macos_language,
                   lambda: os.environ.get("LC_ALL") or os.environ.get("LANG")):
        lang = match(source())
        if lang:
            return lang
    return FALLBACK


def lang() -> str:
    return _forced or _detect()


def set_lang(code: str | None) -> None:
    """For tests and the CLI. None restores auto-detection."""
    global _forced
    _forced = match(code) if code else None


def t(key: str, **kw) -> str:
    text = catalog(lang()).get(key) or catalog(FALLBACK).get(key) or key
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text


def tr(message: "tuple[str, dict] | None") -> str:
    """Turn a (key, args) message from runtime or discover into text."""
    if not message:
        return ""
    key, kw = message
    return t(key, **{k: tr(v) if isinstance(v, tuple) else v for k, v in kw.items()})
