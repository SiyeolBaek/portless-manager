"""portless 의 실행 상태를 읽고, 서비스를 띄우고 끈다.

상태의 정본은 portless 자신의 `~/.portless/routes.json` 이다 (`{hostname, port, pid}`, pid 는
portless CLI 프로세스). 이 도구가 따로 두는 것은 「내가 띄웠는데 아직 route 가 없는」 구간을
알기 위한 기동 기록뿐이다 — 없으면 시작 중과 시작 실패를 구분할 수 없다.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .discover import Target

STATE_DIR = Path(os.environ.get("PORTLESS_STATE_DIR") or Path.home() / ".portless")
APP_DIR = Path.home() / "Library/Application Support/portless-manager"
LAUNCH_DIR = APP_DIR / "launch"
LOG_DIR = Path.home() / "Library/Logs/portless-manager"
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
STOP_WAIT = 8.0
PROXY_DOWN = "프록시가 꺼져 있습니다 — 메뉴에서 「프록시 시작」을 먼저 누르세요"


def env() -> dict:
    """SwiftBar 는 로그인 셸의 PATH 를 물려주지 않는다 — node 24+ 가 있는 경로를 앞에 붙인다."""
    e = dict(os.environ)
    e["PATH"] = EXTRA_PATH + (":" + e["PATH"] if e.get("PATH") else "")
    e.setdefault("NO_COLOR", "1")
    return e


def portless_bin() -> str | None:
    return shutil.which("portless", path=env()["PATH"])


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # 다른 사용자(root) 소유 — 살아 있다
        return True
    return True


# ── portless 상태 ────────────────────────────────────────────────────────
@dataclass
class Route:
    hostname: str
    port: int
    pid: int                    # 0 = `portless alias` 로 건 정적 route

    @property
    def static(self) -> bool:
        return self.pid == 0


def routes() -> list[Route]:
    try:
        raw = json.loads((STATE_DIR / "routes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        try:
            rt = Route(str(r["hostname"]), int(r["port"]), int(r["pid"]))
        except (KeyError, TypeError, ValueError):
            continue
        if rt.static or alive(rt.pid):       # portless 도 같은 기준으로 죽은 route 를 거른다
            out.append(rt)
    return out


def _read(name: str) -> str | None:
    try:
        return (STATE_DIR / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def tlds() -> list[str]:
    raw = _read("proxy.tlds")
    if raw:
        try:
            vals = json.loads(raw) if raw.startswith("[") else \
                [s.strip() for line in raw.splitlines() for s in line.split(",")]
            vals = [v for v in vals if isinstance(v, str) and v]
            if vals:
                return vals
        except ValueError:
            pass
    return [_read("proxy.tld") or "localhost"]


@dataclass
class Proxy:
    running: bool
    pid: int | None
    port: int
    tls: bool


def proxy() -> Proxy:
    try:
        port = int(_read("proxy.port") or 443)
    except ValueError:
        port = 443
    try:
        pid = int(_read("proxy.pid") or 0) or None
    except ValueError:
        pid = None
    tls = (_read("proxy.tls") or "1") not in ("0", "false")
    return Proxy(running=bool(pid and alive(pid)), pid=pid, port=port, tls=tls)


def url(hostname: str, px: Proxy | None = None) -> str:
    px = px or proxy()
    scheme = "https" if px.tls else "http"
    default = 443 if px.tls else 80
    return f"{scheme}://{hostname}" + ("" if px.port == default else f":{px.port}")


def hostnames(t: Target) -> list[str]:
    return [f"{t.name}.{tld}" for tld in tlds()]


# ── 기동 기록 ────────────────────────────────────────────────────────────
def _launch_file(t: Target) -> Path:
    return LAUNCH_DIR / f"{t.key}.json"


def log_file(t: Target) -> Path:
    return LOG_DIR / f"{t.name}.log"


def _load_launch(t: Target) -> dict | None:
    try:
        d = json.loads(_launch_file(t).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _save_launch(t: Target, d: dict) -> None:
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _launch_file(t).with_suffix(".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, _launch_file(t))


def _clear_launch(t: Target) -> None:
    try:
        _launch_file(t).unlink()
    except OSError:
        pass


# ── 상태 판정 ────────────────────────────────────────────────────────────
RUNNING, STARTING, FAILED, STOPPED, UNRUNNABLE = "running", "starting", "failed", "stopped", "unrunnable"


@dataclass
class Status:
    state: str
    route: Route | None = None
    launched_at: float | None = None


def status(t: Target, live: list[Route]) -> Status:
    names = set(hostnames(t))
    route = next((r for r in live if r.hostname in names), None)
    rec = _load_launch(t)
    if route:
        if rec and not rec.get("ran"):     # 한 번이라도 떴다 — 이후 사라지면 실패가 아니라 정지
            rec["ran"] = True
            _save_launch(t, rec)
        return Status(RUNNING, route)
    if rec:
        if alive(int(rec.get("pid", 0))):
            return Status(STARTING, launched_at=rec.get("at"))
        if not rec.get("ran"):
            return Status(FAILED, launched_at=rec.get("at"))
        _clear_launch(t)
    return Status(STOPPED if t.runnable else UNRUNNABLE)


# ── 동작 ──────────────────────────────────────────────────────────────────
def start(t: Target) -> str:
    if not t.runnable:
        return f"실행할 수 없음: {t.note}"
    st = status(t, routes())
    if st.state in (RUNNING, STARTING):
        return "이미 실행 중"
    exe = portless_bin()
    if not exe:
        return "portless 를 찾을 수 없음"
    if not proxy().running:
        # 443 프록시는 sudo 가 필요한데 SwiftBar 에는 TTY 가 없다 — 띄워 봐야 즉시 실패한다
        return PROXY_DOWN
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = log_file(t)
    with open(log, "w", encoding="utf-8") as f:
        f.write(f"# portless-manager {time.strftime('%Y-%m-%d %H:%M:%S')}  {t.path}\n")
        f.flush()
        # 새 세션으로 떼어 SwiftBar 가 끝나도 살아 있게 한다. 인자 없는 `portless` 는
        # portless.json / package.json 의 스크립트를 그 디렉터리의 이름 규칙으로 띄운다.
        p = subprocess.Popen([exe], cwd=t.path, env=env(), stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    _save_launch(t, {"pid": p.pid, "at": time.time(), "path": str(t.path), "name": t.name})
    return f"시작: {t.name}"


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return []
    return [int(x) for x in out.split()]


def _terminate(pid: int) -> bool:
    """SIGTERM → 대기 → 남으면 자식 프로세스 그룹까지 SIGKILL.

    portless 는 앱을 `detached` 로 띄워(별도 프로세스 그룹) SIGTERM 을 받으면 스스로 앱을
    정리하고 route 를 지운다. 그게 안 되면 자식 그룹을 직접 죽인다 — 안 그러면 `next dev` 가
    고아로 포트를 쥐고 남는다.
    """
    kids = _children(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.time() + STOP_WAIT
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.2)
    for k in kids + _children(pid):
        try:
            os.killpg(k, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    time.sleep(0.3)
    return not alive(pid)


def stop(t: Target) -> str:
    live = routes()
    names = set(hostnames(t))
    pids = {r.pid for r in live if r.hostname in names and not r.static}
    rec = _load_launch(t)
    if rec and alive(int(rec.get("pid", 0))):
        pids.add(int(rec["pid"]))
    _clear_launch(t)
    if not pids:
        return "실행 중이 아님"
    ok = all(_terminate(p) for p in pids)
    return f"종료: {t.name}" if ok else f"종료 실패: {t.name} (권한?)"


def stop_all() -> str:
    pids = {r.pid for r in routes() if not r.static}
    for f in LAUNCH_DIR.glob("*.json") if LAUNCH_DIR.is_dir() else []:
        try:
            pid = int(json.loads(f.read_text()).get("pid", 0))
        except (OSError, ValueError):
            pid = 0
        if alive(pid):
            pids.add(pid)
        f.unlink(missing_ok=True)
    ok = sum(_terminate(p) for p in pids)
    return f"{ok}/{len(pids)}개 종료"


def run_portless(*args: str, timeout: float = 60) -> tuple[int, str]:
    exe = portless_bin()
    if not exe:
        return 127, "portless 를 찾을 수 없음"
    try:
        p = subprocess.run([exe, *args], env=env(), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "시간 초과"
    return p.returncode, (p.stdout + p.stderr).strip()


def proxy_command(action: str, px: "Proxy | None" = None) -> str:
    """터미널에서 실행할 프록시 명령. 1024 미만 포트는 sudo 가 필요하다."""
    px = px or proxy()
    sudo = "sudo " if px.port < 1024 else ""
    if action == "start":
        return f"{sudo}portless proxy start --port {px.port}" + (" --https" if px.tls else "")
    return f"{sudo}portless proxy stop"
