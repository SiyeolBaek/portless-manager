"""Desktop notifications, with a click action where the backend allows it.

Backends, tried in order:

1. SwiftBar (`swiftbar://notify`). Clicking opens `href`, which must be a web link; a file://
   link does not open. Known issue: on macOS 26, SwiftBar 2.1.1 also shows its "SwiftBar is
   already running" dialog whenever a plugin notification is clicked, with or without a link
   (https://github.com/swiftbar/SwiftBar/issues/535). The link still opens.
2. `osascript display notification`, if SwiftBar can't be reached. It can't carry a click
   action, so it gets the `plain` wording.

terminal-notifier was tried as a cleaner clickable backend and dropped: the Homebrew build is
ad-hoc signed, and macOS 26 never registers it for notification permission.
"""
from __future__ import annotations

import subprocess
import urllib.parse

PLUGIN = "portless-manager"


def _as(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def commands(title: str, body: str, *, subtitle: str = "", href: str = "", plain: str = "") -> list[list[str]]:
    """argv lists to try in order. `plain` is the text for a backend without a click action."""
    body, plain = body[:200], (plain or body)[:200]
    q = {"plugin": PLUGIN, "title": title, "body": body if href else plain}
    if subtitle:
        q["subtitle"] = subtitle
    if href:
        q["href"] = href
    return [
        ["open", "-g", "swiftbar://notify?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)],
        ["osascript", "-e", f"display notification {_as(plain)} with title {_as(title)}"],
    ]


def send(title: str, body: str, **kw) -> str:
    """Post through the first backend that succeeds. Returns the program that delivered it."""
    for c in commands(title, body, **kw):
        try:
            if subprocess.run(c, capture_output=True, timeout=10).returncode == 0:
                return c[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return ""
