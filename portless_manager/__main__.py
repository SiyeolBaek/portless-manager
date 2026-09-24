"""python3 -m portless_manager <command> — the entry point the SwiftBar wrapper calls."""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import runtime as rt
from . import i18n
from .discover import CONFIG, Target, config_roots, discover
from .i18n import t as _, tr
from .menu import Menu, RED, BASE

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "portless-manager.10s.sh"


def notify(msg: "str | rt.Msg", title: str = "portless") -> None:
    msg = tr(msg) if isinstance(msg, tuple) else msg
    script = f"display notification {_as(msg[:200])} with title {_as(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True)


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
    o = sub.add_parser("open-app")
    o.add_argument("app")
    o.add_argument("path")
    sub.add_parser("copy").add_argument("text")
    sub.add_parser("proxy").add_argument("action", choices=["start", "stop"])
    sub.add_parser("term").add_argument("command")
    for c in ("stop-all", "prune", "doctor", "list", "install", "config"):
        sub.add_parser(c)
    ap.add_argument("--lang", help="override the UI language (e.g. en, ko)")
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
        notify(rt.start(find_target(a.path)))
    elif a.cmd == "stop":
        notify(rt.stop(find_target(a.path)))
    elif a.cmd == "restart":
        t = find_target(a.path)
        rt.stop(t)
        time.sleep(0.5)
        notify(rt.start(t), _("notify.restart"))
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


WRAPPER = """#!/bin/sh
# portless-manager menu bar plugin. Generated by `python3 -m portless_manager install`; do not edit.
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
cd {root} 2>/dev/null || {{ echo "⚠ | sfimage=exclamationmark.triangle color=#d03b3b"; echo "---"; echo {missing}; exit 0; }}
if [ $# -gt 0 ]; then
  exec {py} -m portless_manager "$@"
fi
exec {py} -m portless_manager render
"""


def install() -> None:
    py = shutil.which("python3", path=rt.EXTRA_PATH) or sys.executable
    d = plugin_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / PLUGIN_NAME
    q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"
    tmp = d / f".{PLUGIN_NAME}.tmp"   # hidden name, so SwiftBar doesn't load it as a plugin
    tmp.write_text(WRAPPER.format(root=q(ROOT), py=q(py), missing=q(_("wrapper.missing", root=ROOT))),
                   encoding="utf-8")
    tmp.chmod(0o755)
    tmp.replace(dest)
    print(_("cli.installed", dest=dest, py=py, root=ROOT))


if __name__ == "__main__":
    main()
