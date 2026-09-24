import json
import os
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
    """portless 0.15 cli.js 의 이름 규칙과 같아야 한다."""

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
        write(ws / "c" / "package.json", {"name": "c", "scripts": {"dev": "vite"}})   # portless 안 씀
        git(ws / "a", "init", "-q", "-b", "main")
        git(ws / "a", "add", ".")
        git(ws / "a", "commit", "-qm", "init")
        git(ws / "a", "worktree", "add", "-q", "-b", "feat/x", str(ws / "a-x"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_projects_and_worktrees(self):
        ps = dc.discover([self.root / "work", self.root / "missing"])
        self.assertEqual([p.dirname for p in ps], ["a", "b"])   # a-x 는 a 밑으로, c 는 제외
        a, b = ps
        self.assertEqual(a.main.name, "alpha")
        self.assertEqual([(w.name, w.branch) for w in a.worktrees], [("x.alpha", "feat/x")])
        self.assertEqual(a.workspace, "work")
        self.assertFalse(b.main.runnable)
        self.assertIn("dev", b.main.note)


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
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.STOPPED)   # 한 번 떴으니 실패 아님

    def test_failed_when_launcher_died_before_route(self):
        rt._save_launch(self.t, {"pid": 999999, "at": 0})
        self.assertEqual(rt.status(self.t, rt.routes()).state, rt.FAILED)

    def test_dead_routes_ignored_and_custom_tld(self):
        write(rt.STATE_DIR / "proxy.tlds", "test\n")
        self.routes([{"hostname": "p.test", "port": 1, "pid": 999999}])
        self.assertEqual(rt.routes(), [])
        self.assertEqual(rt.hostnames(self.t), ["p.test"])


if __name__ == "__main__":
    unittest.main()
