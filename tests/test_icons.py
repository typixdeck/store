"""Independent checks of offline icon identity and corruption handling."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from typix_store.icons import icon_file


ASSETS = Path(__file__).resolve().parents[1] / "assets/catalog-icons"


class BundledIconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = "typix-test.svg"
        self.payload = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"/>'
        (self.root / self.name).write_bytes(self.payload)
        self.data = {
            "schema": 1,
            "icons": {"typix-test": {
                "filename": self.name,
                "bytes": len(self.payload),
                "sha256": hashlib.sha256(self.payload).hexdigest(),
            }},
        }
        self.save()

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.data))

    def test_all_actual_bundled_assets_load_within_runtime_limit(self):
        manifest = json.loads((ASSETS / "manifest.json").read_text())
        self.assertEqual(len(manifest["icons"]), 22)
        for package, row in manifest["icons"].items():
            with self.subTest(package=package):
                path = icon_file(package, ASSETS)
                self.assertEqual(path, ASSETS / row["filename"])
                self.assertLessEqual(path.stat().st_size, 2 * 1024 * 1024)
                self.assertEqual(path.stat().st_size, row["bytes"])

    def test_valid_icon_and_unknown_package(self):
        self.assertEqual(icon_file("typix-test", self.root), self.root / self.name)
        self.assertIsNone(icon_file("unknown", self.root))

    def test_byte_corruption_fails_closed(self):
        (self.root / self.name).write_bytes(self.payload + b" ")
        self.assertIsNone(icon_file("typix-test", self.root))

    def test_declared_size_corruption_fails_closed(self):
        self.data["icons"]["typix-test"]["bytes"] += 1
        self.save()
        self.assertIsNone(icon_file("typix-test", self.root))

    def test_declared_size_must_be_positive_integer(self):
        for value in [None, True, "74", 0, -1, len(self.payload) + 0.0]:
            with self.subTest(size=value):
                self.data["icons"]["typix-test"]["bytes"] = value
                self.save()
                self.assertIsNone(icon_file("typix-test", self.root))

    def test_schema_must_be_integer_one(self):
        for value in [True, 1.0, "1", 2, None]:
            with self.subTest(schema=value):
                self.data["schema"] = value
                self.save()
                self.assertIsNone(icon_file("typix-test", self.root))

    def test_bad_hash_fails_closed(self):
        for value in ["0" * 64, "", None, 1]:
            with self.subTest(sha256=value):
                self.data["icons"]["typix-test"]["sha256"] = value
                self.save()
                self.assertIsNone(icon_file("typix-test", self.root))

    def test_filename_cannot_escape_asset_directory(self):
        for value in ["../typix-test.svg", "/tmp/test.svg", "nested/test.png", "https://example.invalid/icon.svg", "test.jpg", None]:
            with self.subTest(filename=value):
                self.data["icons"]["typix-test"]["filename"] = value
                self.save()
                self.assertIsNone(icon_file("typix-test", self.root))

    def test_symlink_and_missing_icon_fail_closed(self):
        path = self.root / self.name
        path.unlink()
        self.assertIsNone(icon_file("typix-test", self.root))
        original = self.root / "original.svg"
        original.write_bytes(self.payload)
        path.symlink_to(original.name)
        self.assertIsNone(icon_file("typix-test", self.root))

    def test_oversized_icon_rejected_even_with_matching_manifest(self):
        payload = b"x" * (2 * 1024 * 1024 + 1)
        (self.root / self.name).write_bytes(payload)
        self.data["icons"]["typix-test"].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        self.save()
        self.assertIsNone(icon_file("typix-test", self.root))

    def test_corrupt_or_oversized_manifest_fails_closed(self):
        for payload in [b"{", b"[]", b"null", b" " * (64 * 1024 + 1)]:
            with self.subTest(bytes=len(payload)):
                (self.root / "manifest.json").write_bytes(payload)
                self.assertIsNone(icon_file("typix-test", self.root))

    def test_declared_theme_aliases_are_bounded_and_match_launcher(self):
        manifest = json.loads((ASSETS / "manifest.json").read_text())
        self.assertEqual(manifest["icons"]["typix-launcher"]["iconName"], "ai.typixdeck.launcher")
        for package, row in manifest["icons"].items():
            with self.subTest(package=package):
                self.assertRegex(row["iconName"], r"^[a-z0-9][a-z0-9.-]*$")
                if package != "typix-launcher":
                    self.assertEqual(package, row["iconName"])


if __name__ == "__main__":
    unittest.main()
