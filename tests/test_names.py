import copy
import unittest

from typix_store.catalog import StoreApp
from typix_store.names import display_name


class DisplayNameTests(unittest.TestCase):
    def test_standalone_brand_prefixes(self):
        cases = {"Typix 阅读器": "阅读器", "TypixDeck 商店": "商店",
                 "TypixDeck Launcher": "Launcher", "Typix-Piano": "Piano",
                 "TypixDeck - Calculator": "Calculator", "Typix — 日历": "日历",
                 "typixdeck\tMail": "Mail", "  Typix Reader  ": "Reader"}
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(display_name(original), expected)

    def test_branding_inside_names_and_unrelated_names_are_preserved(self):
        for name in ("Typixology", "TypixReader", "TypixNode Reader", "My Typix Tool",
                     "Typix", "TypixDeck", "Typix - ", "", "  Calendar  ", "微信", "Copilot"):
            with self.subTest(name=name):
                self.assertEqual(display_name(name), name)

    def test_localized_titles_are_clean_without_changing_signed_record(self):
        raw = {"id": "ai.typixdeck.reader", "package": "typix-reader",
               "desktopFile": "typix-reader.desktop",
               "name": {"zh-CN": "Typix 阅读器", "en": "Typix Reader"}}
        original = copy.deepcopy(raw)
        app = StoreApp(raw)
        self.assertEqual(app.name(), "阅读器")
        self.assertEqual(app.name("en"), "Reader")
        self.assertEqual(raw, original)
        self.assertEqual(app.id, "ai.typixdeck.reader")
        self.assertEqual(app.package, "typix-reader")
        self.assertEqual(app.desktop_file, "typix-reader.desktop")


if __name__ == "__main__":
    unittest.main()
