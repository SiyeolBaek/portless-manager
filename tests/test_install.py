import subprocess
import tempfile
import unittest
from pathlib import Path

from portless_manager import __version__
from portless_manager.__main__ import ROOT, wrapper_text


def sh_ok(text: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(text)
    return subprocess.run(["sh", "-n", f.name]).returncode == 0


class WrapperTest(unittest.TestCase):
    def test_checkout_mode_runs_from_the_checkout(self):
        t = wrapper_text(launcher="", py="/usr/bin/python3")
        self.assertIn(f"cd '{ROOT}'", t)
        self.assertIn("exec '/usr/bin/python3' -m portless_manager render", t)
        self.assertTrue(sh_ok(t))

    def test_launcher_mode_never_embeds_a_versioned_path(self):
        t = wrapper_text(launcher="/opt/homebrew/bin/portless-manager")
        self.assertIn("exec '/opt/homebrew/bin/portless-manager' render", t)
        self.assertNotIn(str(ROOT), t)
        self.assertTrue(sh_ok(t))

    def test_quotes_paths_with_spaces_and_quotes(self):
        t = wrapper_text(launcher="/tmp/it's here/pm")
        self.assertIn("'/tmp/it'\\''s here/pm'", t)
        self.assertTrue(sh_ok(t))


class VersionTest(unittest.TestCase):
    def test_cli_version(self):
        out = subprocess.run(["python3", "-m", "portless_manager", "--version"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, f"portless-manager {__version__}")


if __name__ == "__main__":
    unittest.main()
