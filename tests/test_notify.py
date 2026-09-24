import unittest
import urllib.parse
from unittest import mock

from portless_manager import notify


def query(cmd):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(cmd[-1]).query)


class CommandsTest(unittest.TestCase):
    def test_swiftbar_first_and_carries_the_link(self):
        swiftbar, osa = notify.commands("portless", "blog is ready — click to open",
                                        subtitle="https://blog.localhost", href="https://blog.localhost",
                                        plain="blog is ready")
        self.assertEqual(swiftbar[:2], ["open", "-g"])
        q = query(swiftbar)
        self.assertEqual(q["href"], ["https://blog.localhost"])
        self.assertEqual(q["body"], ["blog is ready — click to open"])
        self.assertEqual(q["subtitle"], ["https://blog.localhost"])
        # the fallback can't be clicked, so it must not say so
        self.assertEqual(osa[0], "osascript")
        self.assertIn('"blog is ready"', osa[-1])
        self.assertNotIn("click", osa[-1])

    def test_without_link_uses_plain_text_and_no_href(self):
        swiftbar, _ = notify.commands("portless", "x — click to open", plain="x")
        q = query(swiftbar)
        self.assertNotIn("href", q)
        self.assertEqual(q["body"], ["x"])

    def test_send_falls_back_to_osascript(self):
        calls = []

        def run(c, **kw):
            calls.append(c[0])
            return mock.Mock(returncode=1 if c[0] == "open" else 0)

        with mock.patch.object(notify.subprocess, "run", side_effect=run):
            self.assertEqual(notify.send("portless", "m", href="https://x", plain="p"), "osascript")
        self.assertEqual(calls, ["open", "osascript"])


if __name__ == "__main__":
    unittest.main()
