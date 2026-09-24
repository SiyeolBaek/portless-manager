"""Read portless's runtime state, and start and stop services.

The source of truth is portless's own `~/.portless/routes.json` (`{hostname, port, pid}`, where pid
is the portless CLI process). The only state this tool keeps is a launch record covering "launched,
but no route yet". Without it, starting and failed-to-start would look the same.
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

# User-facing results are returned as (i18n key, args), not sentences; only the display layer translates
Msg = tuple[str, dict]


def msg(key: str, **kw) -> Msg:
    return key, kw

STATE_DIR = Path(os.environ.get("PORTLESS_STATE_DIR") or Path.home() / ".portless")
APP_DIR = Path.home() / "Library/Application Support/portless-manager"
LAUNCH_DIR = APP_DIR / "launch"
LOG_DIR = Path.home() / "Library/Logs/portless-manager"
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
STOP_WAIT = 8.0


def env() -> dict:
    """SwiftBar doesn't inherit the login shell's PATH, so prepend the paths where Node 24+ lives."""
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
    except PermissionError:      # owned by another user (root), so it is alive
        return True
    return True


# ── portless state ───────────────────────────────────────────────────────
@dataclass
class Route:
    hostname: str
    port: int
    pid: int                    # 0 = static route added with `portless alias`

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
        if rt.static or alive(rt.pid):       # portless drops dead routes by the same rule
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


# ── Launch records ───────────────────────────────────────────────────────
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


# ── Status ───────────────────────────────────────────────────────────────
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
        if rec and not rec.get("ran"):     # it came up once, so disappearing later means stopped, not failed
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


# ── Actions ───────────────────────────────────────────────────────────────
def start(t: Target) -> Msg:
    if not t.runnable:
        return msg("result.unrunnable", reason=t.note)
    st = status(t, routes())
    if st.state in (RUNNING, STARTING):
        return msg("result.already_running")
    exe = portless_bin()
    if not exe:
        return msg("result.no_portless")
    if not proxy().running:
        # The 443 proxy needs sudo and SwiftBar has no TTY, so a launch would fail immediately
        return msg("result.proxy_down")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = log_file(t)
    with open(log, "w", encoding="utf-8") as f:
        f.write(f"# portless-manager {time.strftime('%Y-%m-%d %H:%M:%S')}  {t.path}\n")
        f.flush()
        # Detach into a new session so it outlives SwiftBar. Bare `portless` runs the script from
        # portless.json / package.json under that directory's naming rules.
        p = subprocess.Popen([exe], cwd=t.path, env=env(), stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    _save_launch(t, {"pid": p.pid, "at": time.time(), "path": str(t.path), "name": t.name})
    return msg("result.started", name=t.name)


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return []
    return [int(x) for x in out.split()]


def _terminate(pid: int) -> bool:
    """SIGTERM, wait, then SIGKILL the child process groups if it is still alive.

    portless starts the app `detached` (its own process group), and on SIGTERM it shuts the app
    down and removes the route itself. If that doesn't happen, kill the child groups directly.
    Otherwise `next dev` is left orphaned, holding its port.
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


def stop(t: Target) -> Msg:
    live = routes()
    names = set(hostnames(t))
    pids = {r.pid for r in live if r.hostname in names and not r.static}
    rec = _load_launch(t)
    if rec and alive(int(rec.get("pid", 0))):
        pids.add(int(rec["pid"]))
    _clear_launch(t)
    if not pids:
        return msg("result.not_running")
    ok = all(_terminate(p) for p in pids)
    return msg("result.stopped" if ok else "result.stop_failed", name=t.name)


def stop_all() -> Msg:
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
    return msg("result.stopped_all", ok=ok, total=len(pids))


def run_portless(*args: str, timeout: float = 60) -> tuple[int, str]:
    """(exit code, output). 127 if portless is missing, 124 on timeout, with empty output in both."""
    exe = portless_bin()
    if not exe:
        return 127, ""
    try:
        p = subprocess.run([exe, *args], env=env(), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, ""
    return p.returncode, (p.stdout + p.stderr).strip()


def proxy_command(action: str, px: "Proxy | None" = None) -> str:
    """The proxy command to run in Terminal. Ports below 1024 need sudo."""
    px = px or proxy()
    sudo = "sudo " if px.port < 1024 else ""
    if action == "start":
        return f"{sudo}portless proxy start --port {px.port}" + (" --https" if px.tls else "")
    return f"{sudo}portless proxy stop"
