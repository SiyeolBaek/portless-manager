import ast
import os
import re
import string
import unittest
from pathlib import Path
from unittest import mock

from portless_manager import i18n
from portless_manager.menu import Menu

PKG = Path(i18n.__file__).parent
HANGUL = re.compile(r"[가-힣]")


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


class CatalogTest(unittest.TestCase):
    """새 언어 파일은 en.json 과 키·자리표시자가 같아야 한다."""

    def test_every_locale_matches_english(self):
        en = i18n.catalog("en")
        self.assertGreater(len(en), 0)
        for lang in i18n.available():
            cat = i18n.catalog(lang)
            with self.subTest(lang=lang):
                self.assertEqual(set(cat), set(en), "keys differ from en.json")
                for k, v in cat.items():
                    self.assertEqual(fields(v), fields(en[k]), f"placeholders differ: {k}")

    def test_code_uses_only_known_keys(self):
        en = i18n.catalog("en")
        used = set()
        for py in PKG.glob("*.py"):
            src = py.read_text(encoding="utf-8")
            used |= set(re.findall(r'''(?:_|msg|t)\(\s*["']([a-z]+\.[a-z_]+)["']''', src))
            used |= set(re.findall(r'''\(\s*["']((?:note|result)\.[a-z_]+)["']\s*,\s*\{''', src))
        self.assertTrue(used)
        self.assertEqual(used - set(en), set(), "keys used in code but missing from en.json")

    def test_no_hardcoded_hangul_in_code(self):
        """문구는 locales/ 에만. 주석·독스트링은 대상이 아니다."""
        for py in PKG.glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            docstrings = {id(n.value) for n in ast.walk(tree)
                          if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                    with self.subTest(file=py.name, line=n.lineno):
                        self.assertIsNone(HANGUL.search(n.value), n.value[:60])


class ResolveTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang(None)
        i18n._detect.cache_clear()

    def test_match(self):
        self.assertEqual(i18n.match("ko-KR"), "ko")
        self.assertEqual(i18n.match("ko_KR.UTF-8"), "ko")
        self.assertEqual(i18n.match("EN"), "en")
        self.assertIsNone(i18n.match("xx-YY"))
        self.assertIsNone(i18n.match("C"))
        self.assertIsNone(i18n.match(None))

    def test_parse_apple_languages(self):
        self.assertEqual(i18n.parse_apple_languages('(\n    "ko-KR",\n    "en-KR"\n)\n'), "ko-KR")
        self.assertEqual(i18n.parse_apple_languages("(\n    en,\n    ko\n)\n"), "en")
        self.assertIsNone(i18n.parse_apple_languages(""))

    def test_env_wins_and_unknown_falls_back(self):
        with mock.patch.dict(os.environ, {"PORTLESS_MANAGER_LANG": "ko"}):
            i18n._detect.cache_clear()
            self.assertEqual(i18n.lang(), "ko")
        with mock.patch.dict(os.environ, {"PORTLESS_MANAGER_LANG": "xx", "LANG": "C"}), \
                mock.patch.object(i18n, "_config_language", return_value=None), \
                mock.patch.object(i18n, "_macos_language", return_value="xx-XX"):
            i18n._detect.cache_clear()
            self.assertEqual(i18n.lang(), "en")

    def test_missing_key_and_bad_args_do_not_crash(self):
        i18n.set_lang("ko")
        self.assertEqual(i18n.t("no.such_key"), "no.such_key")
        self.assertIn("{name}", i18n.t("result.started"))       # 인자 누락 → 원문 그대로
        self.assertEqual(i18n.tr(("result.unrunnable", {"reason": ("note.no_script", {"script": "dev"})})),
                         '실행할 수 없음: "dev" 스크립트 없음')


class MenuLanguageTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang(None)

    def test_english_menu_has_no_korean(self):
        i18n.set_lang("en")
        out = Menu("/dev/null").render([])
        self.assertIsNone(HANGUL.search(out), out)
        self.assertIn("Refresh", out)

    def test_korean_menu(self):
        i18n.set_lang("ko")
        self.assertIn("새로고침", Menu("/dev/null").render([]))


if __name__ == "__main__":
    unittest.main()
