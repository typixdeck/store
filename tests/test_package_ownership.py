"""Check actual built Store deb ownership against the applications it distributes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

from typix_store import __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from store_publication import package_fields, read_payload


@unittest.skipUnless(shutil.which("dpkg-deb"), "requires real Debian package inspection")
class StorePackageOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = Path(os.environ.get("TYPIX_STORE_TEST_DEB", ROOT / "dist" / f"typix-store_{__version__}-1_all.deb"))
        if not cls.package.is_file():
            raise unittest.SkipTest("build-deb.sh must run before actual package ownership checks")
        cls.owned = set()
        read_payload(cls.package, set(), owned_paths=cls.owned)

    def test_store_exports_only_its_own_standard_theme_icon(self):
        icons = {path for path in self.owned if path.startswith("usr/share/icons/")}
        self.assertEqual(icons, {"usr/share/icons/hicolor/scalable/apps/typix-store.svg"})

    def test_all_22_private_logos_keep_exact_manifest_bytes(self):
        assets = ROOT / "assets/catalog-icons"
        manifest = json.loads((assets / "manifest.json").read_bytes())
        self.assertEqual(len(manifest["icons"]), 22)
        prefix = "usr/share/typix-store/catalog-icons/"
        names = {prefix + row["filename"] for row in manifest["icons"].values()}
        names.add(prefix + "manifest.json")
        files, _ = read_payload(self.package, names)
        self.assertEqual(files[prefix + "manifest.json"].data, (assets / "manifest.json").read_bytes())
        for row in manifest["icons"].values():
            with self.subTest(asset=row["filename"]):
                payload = files[prefix + row["filename"]].data
                self.assertEqual(len(payload), row["bytes"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), row["sha256"])

    def test_no_non_directory_paths_overlap_any_distributed_application(self):
        packages = sorted((ROOT / "debs").glob("*.deb"))
        self.assertEqual(len(packages), 22)
        checked = 0
        for package in packages:
            if package_fields(package)["Package"] == "typix-store":
                continue
            with self.subTest(package=package.name):
                owned = set()
                read_payload(package, set(), owned_paths=owned)
                self.assertEqual(self.owned & owned, set(), "dpkg rejects cross-package files, including identical artwork")
                checked += 1
        self.assertEqual(checked, 21)


if __name__ == "__main__":
    unittest.main()
