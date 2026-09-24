"""python3 -m portless_manager <명령> — SwiftBar 래퍼가 부르는 진입점."""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import runtime as rt
from .discover import CONFIG, Target, config_roots, discover
from .menu import Menu, RED, BASE

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "portless-manager.10s.sh"


def notify(msg: str, title: str = "portless") -> None:
    script = f"display notification {_as(msg[:200])} with title {_as(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True)


def _as(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def in_terminal(cmd: str) -> None:
    """터미널 새 창에서 명령을 실행한다. 출력이 길거나 대화형인 portless 명령용."""
    full = f"export PATH={shlex.quote(rt.EXTRA_PATH)}:$PATH; {cmd}"
    subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script {_as(full)}',
                    "-e", 'tell application "Terminal" to activate'], capture_output=True)


def find_target(path: str) -> Target:
    p = Path(path).resolve()
    for proj in discover():
        for t in [proj.main, *proj.worktrees]:
            if t.path.resolve() == p:
                return t
    raise SystemExit(f"대상이 아님: {path}")


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
    a = ap.parse_args(argv)

    if a.cmd == "render":
        wrapper = a.wrapper or str(plugin_dir() / PLUGIN_NAME)
        try:
            print(Menu(wrapper).render(discover()))
        except Exception as e:      # 메뉴바에 트레이스백 대신 한 줄
            print(f"⚠ | sfimage=exclamationmark.triangle color={RED}\n---\n렌더 실패: {e!r:.150} | {BASE}")
    elif a.cmd == "start":
        notify(rt.start(find_target(a.path)))
    elif a.cmd == "stop":
        notify(rt.stop(find_target(a.path)))
    elif a.cmd == "restart":
        t = find_target(a.path)
        rt.stop(t)
        time.sleep(0.5)
        notify(rt.start(t), "portless 재시작")
    elif a.cmd == "log":
        log = rt.log_file(find_target(a.path))
        subprocess.run(["open", "-a", "Console", str(log)])
    elif a.cmd == "reveal":
        subprocess.run(["open", a.path])
    elif a.cmd == "open-app":
        subprocess.run(["open", "-a", a.app, a.path])
    elif a.cmd == "copy":
        subprocess.run(["pbcopy"], input=a.text, text=True)
        notify(f"복사함: {a.text}")
    elif a.cmd == "proxy":
        in_terminal(rt.proxy_command(a.action))
    elif a.cmd == "stop-all":
        notify(rt.stop_all())
    elif a.cmd == "prune":
        code, out = rt.run_portless("prune")
        notify(out.splitlines()[-1] if out else ("정리할 것 없음" if code == 0 else f"실패 ({code})"),
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
        print(f"config  {CONFIG}" + ("" if CONFIG.exists() else "  (없음 — 기본 루트 사용)"))
        for r in config_roots():
            print(f"  root  {r}" + ("" if r.is_dir() else "  (없음)"))
    elif a.cmd == "install":
        install()


# ── 설치 ──────────────────────────────────────────────────────────────────
def plugin_dir() -> Path:
    try:
        out = subprocess.run(["defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return Path(out).expanduser() if out else Path.home() / "Library/Application Support/SwiftBar/Plugins"


WRAPPER = """#!/bin/sh
# portless-manager 메뉴바 — `python3 -m portless_manager install` 이 생성했다. 손으로 고치지 말 것.
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
cd {root} 2>/dev/null || {{ echo "⚠ | sfimage=exclamationmark.triangle color=#d03b3b"; echo "---"; echo "저장소 없음: {root}"; exit 0; }}
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
    tmp = d / f".{PLUGIN_NAME}.tmp"   # 숨김 이름 — SwiftBar 가 플러그인으로 집어가지 않게
    tmp.write_text(WRAPPER.format(root=q(ROOT), py=q(py)), encoding="utf-8")
    tmp.chmod(0o755)
    tmp.replace(dest)
    print(f"설치  {dest}\n  python  {py}\n  저장소  {ROOT}")


if __name__ == "__main__":
    main()
