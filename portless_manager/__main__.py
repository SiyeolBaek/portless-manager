"""python3 -m portless_manager <command> — the entry point the SwiftBar wrapper calls."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import notify as notifier
from . import runtime as rt
from . import __version__, i18n
from .discover import CONFIG, Target, config_roots, discover
from .i18n import t as _, tr
from .menu import Menu, RED, BASE

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "portless-manager.10s.sh"


def notify(msg: "str | rt.Msg", title: str = "portless", *, subtitle: str = "", href: str = "",
           plain: "str | rt.Msg" = "") -> None:
    """Post a notification. `href` opens on click where the backend supports it (see notify.py);
    `plain` is the wording used when it doesn't."""
    text = lambda m: tr(m) if isinstance(m, tuple) else m
    notifier.send(title, text(msg), subtitle=subtitle, href=href, plain=text(plain))


def launch(t: Target, title: str = "portless") -> None:
    """Start a target, then hand off to a detached watcher that notifies once it's ready.

    The menu click returns right away (the item turns ⏳); the watcher waits in the background.
    """
    result = rt.start(t)
    if result[0] != "result.started":
        notify(result, title)
        return
    env = dict(rt.env(), PORTLESS_MANAGER_LANG=i18n.lang())
    subprocess.Popen([sys.executable, "-m", "portless_manager", "watch", str(t.path), "--title", title],
                     cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def watch(t: Target, title: str) -> None:
    outcome, route = rt.wait_ready(t)
    if outcome == rt.CANCELLED:
        return
    # Redraw now, so a menu opened right after the notification already shows 🟢 (or ⚠️)
    notifier.refresh_menu()
    if outcome == rt.FAILED_EARLY:
        # No href: SwiftBar notifications only open web links, so point to the menu instead
        notify(("result.start_failed", {"name": t.name}), title)
        return
    link = rt.url(route.hostname if route else rt.hostnames(t)[0])
    ready = outcome == rt.READY
    notify(("result.ready" if ready else "result.still_starting", {"name": t.name}), title,
           subtitle=link, href=link,
           plain=("result.ready_plain" if ready else "result.still_starting_plain", {"name": t.name}))


def _as(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def in_terminal(cmd: str) -> None:
    """Run a command in a new Terminal window, for portless commands that are long or interactive."""
    full = f"export PATH={shlex.quote(rt.EXTRA_PATH)}:$PATH; {cmd}"
    subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script {_as(full)}',
                    "-e", 'tell application "Terminal" to activate'], capture_output=True)


def find_target(path: str) -> Target:
    p = Path(path).resolve()
    for proj in discover():
        for t in [proj.main, *proj.worktrees]:
            if t.path.resolve() == p:
                return t
    raise SystemExit(_("cli.not_target", path=path))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="portless_manager")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--wrapper")
    for c in ("start", "stop", "restart", "log", "reveal"):
        sub.add_parser(c).add_argument("path")
    w = sub.add_parser("watch")
    w.add_argument("path")
    w.add_argument("--title", default="portless")
    o = sub.add_parser("open-app")
    o.add_argument("app")
    o.add_argument("path")
    sub.add_parser("copy").add_argument("text")
    sub.add_parser("proxy").add_argument("action", choices=["start", "stop"])
    sub.add_parser("term").add_argument("command")
    for c in ("stop-all", "prune", "doctor", "list", "install", "config"):
        sub.add_parser(c)
    ap.add_argument("--lang", help="override the UI language (e.g. en, ko)")
    ap.add_argument("--version", action="version", version=f"portless-manager {__version__}")
    a = ap.parse_args(argv)
    if a.lang:
        i18n.set_lang(a.lang)

    if a.cmd == "render":
        wrapper = a.wrapper or str(plugin_dir() / PLUGIN_NAME)
        try:
            print(Menu(wrapper).render(discover()))
        except Exception as e:      # one line in the menu bar instead of a traceback
            err = _("menu.render_failed", error=repr(e)[:150])
            print(f"⚠ | sfimage=exclamationmark.triangle color={RED}\n---\n{err} | {BASE}")
    elif a.cmd == "start":
        launch(find_target(a.path))
    elif a.cmd == "watch":
        watch(find_target(a.path), a.title)
    elif a.cmd == "stop":
        notify(rt.stop(find_target(a.path)))
    elif a.cmd == "restart":
        t = find_target(a.path)
        rt.stop(t)
        time.sleep(0.5)
        launch(t, _("notify.restart"))
    elif a.cmd == "log":
        log = rt.log_file(find_target(a.path))
        subprocess.run(["open", "-a", "Console", str(log)])
    elif a.cmd == "reveal":
        subprocess.run(["open", a.path])
    elif a.cmd == "open-app":
        subprocess.run(["open", "-a", a.app, a.path])
    elif a.cmd == "copy":
        subprocess.run(["pbcopy"], input=a.text, text=True)
        notify(_("result.copied", text=a.text))
    elif a.cmd == "proxy":
        in_terminal(rt.proxy_command(a.action))
    elif a.cmd == "stop-all":
        notify(rt.stop_all())
    elif a.cmd == "prune":
        code, out = rt.run_portless("prune")
        if code == 127:
            notify(("result.no_portless", {}), "portless prune")
        elif code == 124:
            notify(("result.timeout", {}), "portless prune")
        else:
            notify(out.splitlines()[-1] if out else
                   _("result.prune_none") if code == 0 else _("result.failed_code", code=code),
                   "portless prune")
    elif a.cmd == "doctor":
        in_terminal("portless doctor")
    elif a.cmd == "term":
        in_terminal(a.command)
    elif a.cmd == "list":
        for p in discover():
            live = rt.routes()
            for t in [p.main, *p.worktrees]:
                st = rt.status(t, live)
                print(f"{p.workspace:14} {t.name:28} {st.state:10} {t.path}")
    elif a.cmd == "config":
        print(f"config  {CONFIG}" + ("" if CONFIG.exists() else "  " + _("cli.config_missing")))
        for r in config_roots():
            print(f"  root  {r}" + ("" if r.is_dir() else "  " + _("cli.root_missing")))
        print(_("cli.language", lang=i18n.lang(), available=", ".join(i18n.available())))
    elif a.cmd == "install":
        install()


# ── Install ───────────────────────────────────────────────────────────────
def plugin_dir() -> Path:
    try:
        out = subprocess.run(["defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return Path(out).expanduser() if out else Path.home() / "Library/Application Support/SwiftBar/Plugins"


HEADER = """#!/bin/sh
# portless-manager menu bar plugin. Generated by `portless-manager install`; do not edit.
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
"""
MISSING = 'echo "⚠ | sfimage=exclamationmark.triangle color=#d03b3b"; echo "---"; echo {missing}; exit 0;'

# Run from a checkout: remember the checkout and the interpreter.
FROM_CHECKOUT = HEADER + """cd {root} 2>/dev/null || {{ """ + MISSING + """ }}
if [ $# -gt 0 ]; then
  exec {py} -m portless_manager "$@"
fi
exec {py} -m portless_manager render
"""

# Run from a package manager: call its stable launcher. A Homebrew Cellar path contains the
# version and disappears on upgrade, so the formula sets PORTLESS_MANAGER_LAUNCHER to the
# unversioned `bin/portless-manager` instead.
FROM_LAUNCHER = HEADER + """[ -x {launcher} ] || {{ """ + MISSING + """ }}
if [ $# -gt 0 ]; then
  exec {launcher} "$@"
fi
exec {launcher} render
"""


def _q(s) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def wrapper_text(launcher: str | None = None, py: str | None = None) -> str:
    launcher = launcher if launcher is not None else os.environ.get("PORTLESS_MANAGER_LAUNCHER")
    if launcher:
        return FROM_LAUNCHER.format(launcher=_q(launcher), missing=_q(_("wrapper.missing", root=launcher)))
    py = py or shutil.which("python3", path=rt.EXTRA_PATH) or sys.executable
    return FROM_CHECKOUT.format(root=_q(ROOT), py=_q(py), missing=_q(_("wrapper.missing", root=ROOT)))


def install() -> None:
    d = plugin_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / PLUGIN_NAME
    tmp = d / f".{PLUGIN_NAME}.tmp"   # hidden name, so SwiftBar doesn't load it as a plugin
    tmp.write_text(wrapper_text(), encoding="utf-8")
    tmp.chmod(0o755)
    tmp.replace(dest)
    launcher = os.environ.get("PORTLESS_MANAGER_LAUNCHER")
    print(_("cli.installed", dest=dest, py=launcher or sys.executable, root=launcher or ROOT))


if __name__ == "__main__":
    main()
