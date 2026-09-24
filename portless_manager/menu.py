"""SwiftBar 메뉴 출력. 클릭은 전부 설치된 래퍼(`portless-manager.10s.sh <명령> …`)로 돌아온다."""
from __future__ import annotations

import time
from pathlib import Path

from . import runtime as rt
from .discover import CONFIG, Project, Target
from .i18n import t as _, tr

GREEN, AMBER, RED = "#2f9e44", "#d9a400", "#d03b3b"
TEXT = "#1d1d1f,#f2f2f0"
MUTED = "#8a8a86,#8a8a86"
BASE = "symbolize=false emojize=false trim=false"
ICON = {rt.RUNNING: "🟢", rt.STARTING: "⏳", rt.FAILED: "⚠️", rt.STOPPED: "⚪", rt.UNRUNNABLE: "⛔"}

EDITORS = ("Cursor", "Visual Studio Code", "Zed", "WebStorm", "IntelliJ IDEA")
TERMINALS = ("Ghostty", "iTerm", "Warp", "Terminal")


def _q(s) -> str:
    s = str(s)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _app(names: tuple[str, ...]) -> str | None:
    for n in names:
        for base in (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications/Utilities")):
            if (base / f"{n}.app").exists():
                return n
    return None


class Menu:
    def __init__(self, wrapper: str):
        self.wrapper = wrapper
        self.lines: list[str] = []
        self.px = rt.proxy()
        self.live = rt.routes()
        self.editor = _app(EDITORS)
        self.terminal = _app(TERMINALS) or "Terminal"

    def add(self, depth: int, text: str, params: str = "") -> None:
        self.lines.append(f"{'--' * depth}{text} | {params} {BASE}".rstrip())

    def sep(self, depth: int = 0) -> None:
        self.lines.append("--" * depth + "---")

    def act(self, *args) -> str:
        ps = " ".join(f"param{i + 1}={_q(a)}" for i, a in enumerate(args))
        return f"bash={_q(self.wrapper)} {ps} terminal=false refresh=true"

    def group(self, depth: int, text: str, color: str = TEXT) -> None:
        """하위메뉴 부모에는 항상 color 를 단다. SwiftBar 2.1.1 은 파라미터 없는 하위메뉴 부모의
        동작을 AppKit 에 맡기는데, 새로고침으로 제목이 바뀐 부모는 비활성으로 남아 안의 항목을
        누를 수 없게 된다. `color` 가 있는 줄은 SwiftBar 가 직접 동작을 달아 이 경로를 피한다."""
        self.add(depth, text, f"color={color}")

    # ── 대상 하나 ────────────────────────────────────────────────────────
    def target_items(self, d: int, t: Target, st: rt.Status) -> None:
        host = st.route.hostname if st.route else rt.hostnames(t)[0]
        link = rt.url(host, self.px)
        if st.state == rt.RUNNING:
            self.add(d, f"↗ {link}", f"href={_q(link)}")
            self.add(d, _("target.stop"), self.act("stop", t.path))
            self.add(d, _("target.restart"), self.act("restart", t.path))
        elif st.state == rt.STARTING:
            self.add(d, _("target.starting"), f"color={AMBER}")
            self.add(d, _("target.cancel"), self.act("stop", t.path))
        elif st.state == rt.UNRUNNABLE:
            self.add(d, _("target.unrunnable", reason=tr(t.note)), f"color={MUTED}")
        elif not self.px.running:
            if st.state == rt.FAILED:
                self.add(d, _("target.failed"), f"color={RED}")
            self.add(d, _("target.start_proxy_down"), f"color={MUTED}")
        else:
            if st.state == rt.FAILED:
                self.add(d, _("target.failed"), f"color={RED}")
            self.add(d, _("target.start"), self.act("start", t.path))
        self.add(d, _("target.copy_url"), self.act("copy", link))
        self.sep(d)
        if self.editor:
            self.add(d, _("target.open_in", app=self.editor), self.act("open-app", self.editor, t.path))
        self.add(d, _("target.open_in", app=self.terminal), self.act("open-app", self.terminal, t.path))
        self.add(d, _("target.reveal"), self.act("reveal", t.path))
        log = rt.log_file(t)
        if log.exists():
            self.add(d, _("target.log"), self.act("log", t.path))
        info = [str(t.path).replace(str(Path.home()), "~")]
        if st.route:
            info.append(f"port {st.route.port} · pid {st.route.pid}")
        elif t.note and st.state != rt.UNRUNNABLE:
            info.append(tr(t.note))
        for i in info:
            self.add(d, i, f"color={MUTED} size=11")

    def project(self, p: Project) -> None:
        st = rt.status(p.main, self.live)
        wts = [(w, rt.status(w, self.live)) for w in p.worktrees]
        up = sum(s.state == rt.RUNNING for _, s in wts)
        label = f"{ICON[st.state]} {p.dirname}"
        if st.state == rt.RUNNING:
            label += f"   {st.route.hostname}"
        if wts:
            label += f"   🌿 {up}/{len(wts)}" if up else f"   🌿 {len(wts)}"
        self.group(0, label, TEXT if st.state != rt.UNRUNNABLE else MUTED)
        self.target_items(1, p.main, st)
        if wts:
            self.sep(1)
            self.add(1, _("menu.worktrees"), f"color={MUTED} size=11")
            for w, ws in wts:
                wl = f"{ICON[ws.state]} {w.branch or _('target.detached')}"
                if ws.state == rt.RUNNING:
                    wl += f"   {ws.route.hostname}"
                self.group(1, wl)
                self.target_items(2, w, ws)

    # ── 전체 ──────────────────────────────────────────────────────────────
    def render(self, projects: list[Project]) -> str:
        running = sum(1 for r in self.live if not r.static)
        title = f"{running}" if running else ""
        self.lines.append(f"{title} | sfimage=server.rack {BASE}")
        self.sep()

        if rt.portless_bin() is None:
            self.add(0, _("menu.not_installed"), f"color={RED}")
            return "\n".join(self.lines)

        px = self.px
        if px.running:
            self.group(0, _("proxy.running", port=px.port))
            self.add(1, _("proxy.stop"), self.act("proxy", "stop"))
        else:
            self.group(0, _("proxy.down"), RED)
            self.add(1, _("proxy.start"), self.act("proxy", "start"))
            self.add(1, _("proxy.install"), self.act("term", "portless service install"))
        self.add(1, _("proxy.doctor"), self.act("doctor"))
        self.add(1, _("proxy.list"), self.act("term", "portless list"))

        if running:
            self.add(0, _("menu.stop_all", count=running), self.act("stop-all"))
        self.add(0, _("menu.prune"), self.act("prune"))

        by_ws: dict[str, list[Project]] = {}
        for p in projects:
            by_ws.setdefault(p.workspace, []).append(p)
        for ws, ps in by_ws.items():
            self.sep()
            self.add(0, ws, f"color={MUTED} size=11")
            for p in ps:
                self.project(p)
        if not projects:
            self.sep()
            self.add(0, _("menu.no_projects"), f"color={MUTED}")
            hint = str(CONFIG).replace(str(Path.home()), "~")
            self.add(0, _("menu.config_hint", path=hint), f"color={MUTED} size=11")

        self.sep()
        self.add(0, _("menu.refresh"), "refresh=true")
        self.add(0, _("menu.updated", time=time.strftime("%H:%M:%S")), f"color={MUTED} size=11")
        return "\n".join(self.lines)
