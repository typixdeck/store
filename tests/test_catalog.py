import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from typix_store.catalog import CatalogError, DeviceProfile, incompatibility_reasons, load_catalog_file, validate_catalog, verify_deb_metadata
from typix_store.client import StoreClient, require_system_owned


def catalog():
    now = datetime.now(timezone.utc)
    return {"schemaVersion": 1, "channel": "development-offline", "generatedAt": (now - timedelta(minutes=1)).isoformat(), "expiresAt": (now + timedelta(days=1)).isoformat(), "applications": [{"id": "ai.typixdeck.reader", "package": "typix-reader", "currentVersion": "0.2.0-1", "name": {"zh-CN": "阅读器"}, "desktopFile": "typix-reader.desktop", "versions": [{"version": "0.2.0-1", "artifact": {"filename": "typix-reader_0.2.0-1_all.deb", "arch": "all", "sizeBytes": 1, "sha256": "0" * 64, "depends": "python3, python3-gi"}, "compatibility": {"arch": ["arm64"], "os": ["raspios-bookworm", "raspios-trixie"], "minMemoryMB": 128, "minFreeDiskMB": 64, "display": ["x11", "wayland"], "requiredFeatures": []}}]}]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.public = self.root / "development.pem"
        self.public.write_bytes(self.key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        self.data = catalog()
        self.write_signed()
        self.client = StoreClient(self.root, self.public)
        self.app = validate_catalog(self.data)[0]
        self.profile = DeviceProfile("arm64", "raspios-trixie", 1800, 1024, ("wayland",), frozenset({"wayland"}))

    def write_signed(self):
        contents = json.dumps(self.data).encode()
        (self.root / "catalog.json").write_bytes(contents)
        (self.root / "catalog.json.sig").write_bytes(self.key.sign(contents))

    def test_signed_catalog_loads(self):
        self.assertEqual(self.client.load_catalog()[0].name(), "阅读器")

    def test_unsigned_catalog_rejected(self):
        with self.assertRaises(CatalogError):
            load_catalog_file(self.root / "catalog.json")

    def test_tampered_bytes_rejected(self):
        with (self.root / "catalog.json").open("ab") as output:
            output.write(b" ")
        with self.assertRaises(CatalogError):
            self.client.load_catalog()

    def test_wrong_key_rejected(self):
        self.public.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaises(CatalogError):
            self.client.load_catalog()

    def test_expired_catalog_rejected(self):
        self.data["expiresAt"] = "2000-01-01T00:00:00Z"
        self.write_signed()
        with self.assertRaises(CatalogError):
            self.client.load_catalog()

    def test_future_catalog_rejected(self):
        self.data["generatedAt"] = "2100-01-01T00:00:00Z"
        self.write_signed()
        with self.assertRaises(CatalogError):
            self.client.load_catalog()

    def test_schema_rejects_path_traversal(self):
        for filename in ("../../tmp/evil.deb", "/tmp/evil.deb", ".deb", "foo.deb/evil.deb"):
            with self.subTest(filename=filename):
                bad = copy.deepcopy(self.data)
                bad["applications"][0]["versions"][0]["artifact"]["filename"] = filename
                with self.assertRaises(CatalogError):
                    validate_catalog(bad)

    def test_malformed_version_record_rejected(self):
        self.data["applications"][0]["versions"] = [None]
        with self.assertRaises(CatalogError):
            validate_catalog(self.data)

    def test_runtime_capability_compatibility(self):
        self.assertEqual(incompatibility_reasons(self.app.current_record(), self.profile), [])
        small = DeviceProfile("arm64", "raspios-trixie", 64, 10, ("wayland",), frozenset())
        reasons = incompatibility_reasons(self.app.current_record(), small)
        self.assertTrue(any("内存" in reason for reason in reasons))
        self.assertTrue(any("空间" in reason for reason in reasons))
        # No model gate: runtime capabilities decide independently of CM names.
        self.assertFalse(any("核心" in reason for reason in reasons))

    def test_unknown_os_fails_closed(self):
        profile = DeviceProfile("arm64", "debian-trixie", 2000, 1024, ("wayland",), frozenset())
        self.assertTrue(any("系统" in reason for reason in incompatibility_reasons(self.app.current_record(), profile)))

    def metadata(self, **overrides):
        fields = {"Package": "typix-reader", "Version": "0.2.0-1", "Architecture": "all", "Depends": "python3, python3-gi", "X-Typix-Compatible-OS": "raspios-bookworm, raspios-trixie"}
        fields.update(overrides)
        result = subprocess.CompletedProcess([], 0, "\n".join(f"{key}: {value}" for key, value in fields.items()).encode(), b"")
        return lambda *args, **kwargs: result

    def test_all_required_package_fields_match(self):
        self.assertEqual(verify_deb_metadata(Path("reader.deb"), self.app, runner=self.metadata())["Architecture"], "all")

    def test_metadata_conflicts_rejected(self):
        for key, value in [("Package", "wrong"), ("Version", "0.1.0-1"), ("Architecture", "armhf"), ("Depends", "python3"), ("X-Typix-Compatible-OS", "raspios-trixie")]:
            with self.subTest(key=key):
                with self.assertRaises(CatalogError):
                    verify_deb_metadata(Path("reader.deb"), self.app, runner=self.metadata(**{key: value}))

    def test_changed_catalog_requires_refresh(self):
        self.data["applications"][0]["name"] = "Changed"
        self.write_signed()
        with self.assertRaises(CatalogError):
            self.client.trusted_app(self.app)

    def test_user_writable_install_source_rejected(self):
        file = self.root / "package.deb"
        file.write_bytes(b"x")
        file.chmod(0o666)
        with self.assertRaises(CatalogError):
            require_system_owned(file)

    def test_symlink_source_rejected(self):
        file = self.root / "real.deb"
        file.write_bytes(b"x")
        link = self.root / "link.deb"
        link.symlink_to(file)
        with self.assertRaises(CatalogError):
            require_system_owned(link)

    def test_half_configured_package_is_not_installed(self):
        result = subprocess.CompletedProcess([], 0, "install ok unpacked\n0.2.0-1", "")
        with patch("typix_store.client.subprocess.run", return_value=result):
            self.assertIsNone(self.client.installed_version(self.app))

    def test_package_database_failure_explains_recovery(self):
        result = subprocess.CompletedProcess([], 0, b"unfinished package", b"")
        with patch("typix_store.client.subprocess.run", return_value=result), self.assertRaisesRegex(CatalogError, "管理员"):
            self.client.check_package_database()

    def test_protected_packages_not_removable(self):
        protected = copy.deepcopy(self.data)
        protected["applications"][0]["package"] = "typix-launcher"
        app = validate_catalog(protected)[0]
        with patch.object(self.client, "trusted_app", return_value=app), patch("typix_store.client.require_system_owned"), self.assertRaisesRegex(CatalogError, "系统入口"):
            self.client.prepare_remove(app)

    def test_user_repository_is_browsable_but_transactions_are_disabled(self):
        (self.root / "packages").mkdir()
        self.assertEqual(self.client.load_catalog()[0].name(), "阅读器")
        with patch("typix_store.client.require_system_owned", side_effect=CatalogError("user-owned source")):
            self.assertEqual(self.client.installation_source_error(), "用户目录预览，系统安装待管理员部署")

    def test_system_source_checks_all_trust_paths_and_enables_transactions(self):
        (self.root / "packages").mkdir()
        with patch("typix_store.client.require_system_owned") as check:
            self.assertIsNone(self.client.installation_source_error())
        self.assertEqual([call.args[0] for call in check.call_args_list], [
            self.public, self.root / "catalog.json", self.root / "catalog.json.sig", self.root / "packages"
        ])

    def test_writable_packages_directory_disables_transactions(self):
        (self.root / "packages").mkdir()
        def check(path):
            if path == self.root / "packages":
                raise CatalogError("user-writable packages")
        with patch("typix_store.client.require_system_owned", side_effect=check):
            self.assertEqual(self.client.installation_source_error(), "用户目录预览，系统安装待管理员部署")

    def test_incomplete_system_source_disables_transactions(self):
        with patch("typix_store.client.require_system_owned"):
            self.assertIn("系统安装源不完整", self.client.installation_source_error())

    def test_debian_version_ordering(self):
        self.assertTrue(self.client.has_update("0.9.0-1", "0.10.0-1"))
        self.assertFalse(self.client.has_update("1.0-1", "1.0~beta-1"))

    def test_artifact_verification_cancellation_and_low_disk(self):
        (self.root / "packages").mkdir()
        artifact = self.data["applications"][0]["versions"][0]["artifact"]
        file = self.root / "packages" / artifact["filename"]
        file.write_bytes(b"x")
        with patch("typix_store.client.require_system_owned"), self.assertRaisesRegex(CatalogError, "取消"):
            self.client.prepare_install(self.app, cancelled=lambda: True, profile=self.profile)
        with patch("typix_store.client.require_system_owned"), patch("typix_store.client.shutil.disk_usage", return_value=type("Disk", (), {"free": 1})()), self.assertRaisesRegex(CatalogError, "空间"):
            self.client.prepare_install(self.app, profile=self.profile)

    def test_real_deb_metadata_and_hash(self):
        stage = self.root / "stage"
        (stage / "DEBIAN").mkdir(parents=True)
        (stage / "DEBIAN" / "control").write_text("Package: typix-reader\nVersion: 0.2.0-1\nArchitecture: all\nMaintainer: Test <test@example.invalid>\nDepends: python3, python3-gi\nX-Typix-Compatible-OS: raspios-bookworm, raspios-trixie\nDescription: Store verification fixture\n")
        (self.root / "packages").mkdir()
        artifact = self.data["applications"][0]["versions"][0]["artifact"]
        package = self.root / "packages" / artifact["filename"]
        subprocess.run(["dpkg-deb", "--build", str(stage), str(package)], capture_output=True, check=True)
        artifact["sizeBytes"] = package.stat().st_size
        artifact["sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
        self.write_signed()
        app = self.client.load_catalog()[0]
        with patch("typix_store.client.require_system_owned"), patch.object(self.client, "check_package_database"):
            self.assertEqual(self.client.prepare_install(app, profile=self.profile), package)
            with package.open("r+b") as output:
                output.write(b"BAD")
            with self.assertRaisesRegex(CatalogError, "SHA-256"):
                self.client.prepare_install(app, profile=self.profile)


if __name__ == "__main__":
    unittest.main()
