import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from portless_manager import discover as dc
from portless_manager import runtime as rt


def write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                   check=True, capture_output=True)


class NamingTest(unittest.TestCase):
    """Must match the naming rules in portless 0.15 cli.js."""

    def test_sanitize(self):
        self.assertEqual(dc.sanitize("My_App!!Name"), "my-app-name")
        self.assertEqual(dc.sanitize("--a--b--"), "a-b")
        long = dc.sanitize("x" * 80)
        self.assertEqual(len(long), 63)

    def test_branch_prefix(self):
        self.assertIsNone(dc.branch_prefix("main"))
        self.assertIsNone(dc.branch_prefix("master"))
        self.assertIsNone(dc.branch_prefix("HEAD"))
        self.assertEqual(dc.branch_prefix("feat/Login_Page"), "login-page")

    def test_name_priority(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj_dir"
            write(root / "package.json", {"name": "@scope/Pkg", "scripts": {"dev": "x"}})
            self.assertEqual(dc.make_target(root).name, "pkg")
            write(root / "portless.json", {"name": "custom"})
            self.assertEqual(dc.make_target(root).name, "custom")


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ws = self.root / "work"
        write(ws / "a" / "portless.json", {"name": "alpha"})
        write(ws / "a" / "package.json", {"scripts": {"dev": "next dev"}})
        write(ws / "b" / "package.json", {"name": "b", "devDependencies": {"portless": "^0.15"}})
        write(ws / "c" / "package.json", {"name": "c", "scripts": {"dev": "vite"}})   # does not use portless
        git(ws / "a", "init", "-q", "-b", "main")
        git(ws / "a", "add", ".")
        git(ws / "a", "commit", "-qm", "init")
        git(ws / "a", "worktree", "add", "-q", "-b", "feat/x", str(ws / "a-x"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_projects_and_worktrees(self):
        ps = dc.discover([self.root / "work", self.root / "missing"])
        self.assertEqual([p.dirname for p in ps], ["a", "b"])   # a-x goes under a; c is excluded
        a, b = ps
        self.assertEqual(a.main.name, "alpha")
        self.assertEqual([(w.name, w.branch) for w in a.worktrees], [("x.alpha", "feat/x")])
        self.assertEqual(a.workspace, "work")
        self.assertFalse(b.main.runnable)
        self.assertEqual(b.main.note, ("note.no_script", {"script": "dev"}))


class ConfigTest(unittest.TestCase):
    def test_roots_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            write(cfg, {"roots": ["~/x", d]})
            self.assertEqual(dc.config_roots(cfg), [Path.home() / "x", Path(d)])

    def test_missing_config_uses_existing_defaults(self):
        roots = dc.config_roots(Path("/nonexistent/config.json"))
        self.assertTrue(all(r.is_dir() for r in roots))


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._orig = (rt.STATE_DIR, rt.LAUNCH_DIR)
        rt.STATE_DIR, rt.LAUNCH_DIR = base / "state", base / "launch"
        rt.STATE_DIR.mkdir()
        write(base / "p" / "package.json", {"name": "p", "scripts": {"dev": "x"}})
        self.t = dc.make_target(base / "p")

    def tearDown(self):
        rt.STATE_DIR, rt.LAUNCH_DIR = self._orig
        self.tmp.cleanup()

    def routes(self, rs):
        write(rt.STATE_DIR / "routes.json", rs)

    def test_transitions(self):
        me = os.getpid()
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.STOPPED)
        rt._save_launch(self.t, {"pid": me, "at": 0})
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.STARTING)
        self.routes([{"hostname": "p.localhost", "port": 4000, "pid": me}])
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.RUNNING)
        self.routes([])
        rt._save_launch(self.t, {**rt._load_launch(self.t), "pid": 999999})
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.STOPPED)   # it came up once, so not failed

    def test_failed_when_launcher_died_before_route(self):
        rt._save_launch(self.t, {"pid": 999999, "at": 0})
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.FAILED)

    def test_dead_routes_ignored_and_custom_tld(self):
        write(rt.STATE_DIR / "proxy.tlds", "test\n")
        self.routes([{"hostname": "p.test", "port": 1, "pid": 999999}])
        self.assertEqual(rt.routes(), [])
        self.assertEqual(rt.hostnames(self.t), ["p.test"])


class WaitReadyTest(unittest.TestCase):
    """wait_ready reports ready only once the app port accepts connections."""
    setUp, tearDown, routes = StatusTest.setUp, StatusTest.tearDown, StatusTest.routes

    def test_ready_when_port_listens(self):
        me = os.getpid()
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen()
            port = srv.getsockname()[1]
            rt._save_launch(self.t, {"pid": me, "at": 0})
            self.routes([{"hostname": "p.localhost", "port": port, "pid": me}])
            outcome, route = rt.wait_ready(self.t, timeout=2, poll=0.05)
        self.assertEqual(outcome, rt.READY)
        self.assertEqual(route.port, port)

    def test_route_without_listener_times_out(self):
        me = os.getpid()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]           # bound but not listening
            rt._save_launch(self.t, {"pid": me, "at": 0})
            self.routes([{"hostname": "p.localhost", "port": free, "pid": me}])
            outcome, route = rt.wait_ready(self.t, timeout=0.3, poll=0.05)
        self.assertEqual(outcome, rt.TIMEOUT)
        self.assertIsNotNone(route)

    def test_dies_after_route_before_listening(self):
        me = os.getpid()
        rt._save_launch(self.t, {"pid": me, "at": 0, "ran": True})   # route was seen
        self.routes([])
        rt._save_launch(self.t, {"pid": 999999, "at": 0, "ran": True})
        self.assertEqual(rt.wait_ready(self.t, timeout=1, poll=0.05)[0], rt.FAILED_EARLY)

    def test_failed_and_cancelled(self):
        rt._save_launch(self.t, {"pid": 999999, "at": 0})
        self.assertEqual(rt.wait_ready(self.t, timeout=1, poll=0.05)[0], rt.FAILED_EARLY)
        rt._clear_launch(self.t)
        self.assertEqual(rt.wait_ready(self.t, timeout=1, poll=0.05)[0], rt.CANCELLED)


if __name__ == "__main__":
    unittest.main()
