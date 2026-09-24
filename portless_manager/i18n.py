"""메뉴·알림·CLI 문구 번역. 문구는 전부 `locales/<언어>.json` 에 있고 코드에는 키만 둔다.

언어 결정 순서: `PORTLESS_MANAGER_LANG` → config.json 의 `language` → macOS 시스템 언어
(`AppleLanguages`) → `LC_ALL`/`LANG` → `en`. SwiftBar 같은 GUI 앱은 `LANG` 을 넘겨주지 않는
경우가 많아서 macOS 설정을 먼저 본다.

번역이 없는 키는 `en.json` 으로, 그것도 없으면 키 자체로 보여준다 — 메뉴가 깨지지 않게.
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
    """`ko-KR`·`ko_KR.UTF-8`·`zh-Hans` 같은 태그를 있는 locale 파일에 맞춘다 (전체 → 주 언어)."""
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
    """테스트·CLI 용. None 이면 자동 감지로 되돌린다."""
    global _forced
    _forced = match(code) if code else None


def t(key: str, **kw) -> str:
    text = catalog(lang()).get(key) or catalog(FALLBACK).get(key) or key
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text


def tr(message: "tuple[str, dict] | None") -> str:
    """runtime·discover 가 돌려준 (키, 인자) 를 문장으로."""
    if not message:
        return ""
    key, kw = message
    return t(key, **{k: tr(v) if isinstance(v, tuple) else v for k, v in kw.items()})
