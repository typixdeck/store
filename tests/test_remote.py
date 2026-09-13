import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, Mock

from test_catalog import catalog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from typix_store.catalog import CatalogError, DeviceProfile, validate_catalog
from typix_store.remote import GitHubConfig, GitHubSource, checked_url, fetch_small, load_config
from typix_store.staging import copy_user_file, verify_copied


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None, url="https://github.com/owner/project/releases/latest/download/asset"):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}
        self.url = url
    def geturl(self):
        return self.url


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.root / "key.pem"
        self.public.write_bytes(self.private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        self.config = GitHubConfig("owner/project", self.public)
        self.data = catalog()
        self.data.update(channel="github-release", repository="owner/project")
        self.package = b"complete signed application bytes"
        artifact = self.data["applications"][0]["versions"][0]["artifact"]
        artifact.update(payload="complete-deb", installedSizeBytes=len(self.package), sizeBytes=len(self.package), sha256=hashlib.sha256(self.package).hexdigest())
        self.app = validate_catalog(self.data, expected_repository="owner/project", expected_channel="github-release")[0]
        self.source = GitHubSource(self.config, self.root / "cache")

    def test_urls_derived_from_config_and_filename(self):
        self.assertEqual(self.config.asset_url("reader.deb"), "https://github.com/owner/project/releases/latest/download/reader.deb")
        self.assertEqual(GitHubConfig("owner/project", self.public, release="v0.3").asset_url("reader.deb"), "https://github.com/owner/project/releases/download/v0.3/reader.deb")
        raw = GitHubConfig("owner/project", self.public, mode="raw")
        self.assertEqual(raw.asset_url("reader.deb"), "https://raw.githubusercontent.com/owner/project/main/apps/store/debs/reader.deb")
        with self.assertRaises(CatalogError):
            self.config.asset_url("../evil.deb")

    def test_redirects_reject_http_credentials_and_unrelated_hosts(self):
        for url in ["http://github.com/a", "https://evil.invalid/a", "https://github.com.evil.invalid/a", "https://user:secret@github.com/a", "https://github.com:444/a", "file:///tmp/a"]:
            with self.subTest(url=url), self.assertRaises(CatalogError):
                checked_url(url)
        checked_url("https://release-assets.githubusercontent.com/asset?signature=x")

    def test_root_config_validation(self):
        config = self.root / "github.json"
        config.write_text(json.dumps({"repository": "owner/project", "publicKey": str(self.public)}))
        with patch("typix_store.remote.require_system_owned"):
            self.assertEqual(load_config(config), self.config)
        for field, value in [("repository", "../evil"), ("publicKey", "relative.pem"), ("release", "../../tag"), ("mode", "arbitrary"), ("directory", "../evil")]:
            data = {"repository": "owner/project", "publicKey": str(self.public), field: value}
            config.write_text(json.dumps(data))
            with patch("typix_store.remote.require_system_owned"), self.assertRaises(CatalogError):
                load_config(config)

    def test_remote_wrapper_and_repo_mismatch_rejected(self):
        for change in ("wrapper", "repository"):
            bad = copy.deepcopy(self.data)
            if change == "wrapper":
                bad["applications"][0]["versions"][0]["artifact"]["payload"] = "wrapper"
            else:
                bad["repository"] = "attacker/project"
            with self.assertRaises(CatalogError):
                validate_catalog(bad, expected_repository="owner/project", expected_channel="github-release")

    def test_fetch_limit_and_cancellation(self):
        with self.assertRaisesRegex(CatalogError, "大小"), Response(b"abc") as response:
            fetch_small(self.config.asset_url("catalog.json"), 2, opener=lambda *_: response)
        with self.assertRaisesRegex(CatalogError, "取消"):
            fetch_small(self.config.asset_url("catalog.json"), 20, cancelled=lambda: True, opener=lambda *_: Response(b"abc"))

    def test_signed_catalog_cache_is_offline_fallback(self):
        contents = json.dumps(self.data).encode()
        responses = iter([contents, self.private.sign(contents)])
        self.source.opener = lambda *_: Response(next(responses))
        self.assertEqual(self.source.load()[0].package, "typix-reader")
        self.source.opener = Mock(side_effect=urllib.error.URLError("offline"))
        self.assertEqual(self.source.load()[0].package, "typix-reader")
        self.assertIn("缓存", self.source.notice)
        self.source.catalog.write_bytes(contents + b" ")
        with self.assertRaises(CatalogError):
            self.source.load()

    def test_bad_signature_never_enters_cache(self):
        contents = json.dumps(self.data).encode()
        responses = iter([contents, bytes(64)])
        self.source.opener = lambda *_: Response(next(responses))
        with self.assertRaises(CatalogError):
            self.source.load()
        self.assertFalse((self.source.cache / "current").exists())

    def partial(self, data):
        directory = self.source.cache / "downloads"
        directory.mkdir(parents=True)
        path = directory / (self.app.current_record()["artifact"]["sha256"] + ".part")
        path.write_bytes(data)
        return path

    def test_range_resume_requires_exact_content_range(self):
        self.partial(self.package[:5])
        def opener(url, headers):
            self.assertEqual(headers, {"Range": "bytes=5-"})
            return Response(self.package[5:], 206, {"Content-Range": f"bytes 5-{len(self.package)-1}/{len(self.package)}", "Content-Length": str(len(self.package)-5)})
        self.source.opener = opener
        self.assertEqual(self.source.download(self.app).read_bytes(), self.package)

    def test_ignored_range_restarts_instead_of_appending(self):
        self.partial(b"wrong")
        self.source.opener = lambda *_: Response(self.package, 200)
        self.assertEqual(self.source.download(self.app).read_bytes(), self.package)

    def test_wrong_range_rejected_and_partial_preserved(self):
        partial = self.partial(self.package[:5])
        self.source.opener = lambda *_: Response(self.package[5:], 206, {"Content-Range": f"bytes 4-{len(self.package)-1}/{len(self.package)}"})
        with self.assertRaises(CatalogError):
            self.source.download(self.app)
        self.assertEqual(partial.read_bytes(), self.package[:5])

    def test_hash_failure_removes_poisoned_partial(self):
        self.source.opener = lambda *_: Response(b"x" * len(self.package), 200)
        with self.assertRaisesRegex(CatalogError, "SHA-256"):
            self.source.download(self.app)
        self.assertEqual(list((self.source.cache / "downloads").iterdir()), [])

    def test_download_cancellation_and_low_disk_do_not_install(self):
        self.source.opener = lambda *_: Response(self.package)
        with self.assertRaisesRegex(CatalogError, "取消"):
            self.source.download(self.app, cancelled=lambda: True)
        with patch("typix_store.remote.shutil.disk_usage", return_value=type("Disk", (), {"free": 1})()), self.assertRaisesRegex(CatalogError, "空间"):
            self.source.download(self.app)

    def test_complete_corrupt_partial_is_replaced_on_retry(self):
        self.partial(b"x" * len(self.package))
        self.source.opener = lambda *_: Response(self.package)
        self.assertEqual(self.source.download(self.app).read_bytes(), self.package)

    def test_missing_publication_falls_back_to_real_offline_source(self):
        from typix_store.client import StoreClient
        offline = catalog()
        raw = json.dumps(offline).encode()
        (self.root / "catalog.json").write_bytes(raw)
        (self.root / "catalog.json.sig").write_bytes(self.private.sign(raw))
        config_path = self.root / "config.json"
        config_path.write_text("{}")
        remote = Mock(failure_kind="unpublished")
        remote.load.side_effect = CatalogError("404")
        client = StoreClient(self.root, self.public, github_config=config_path)
        with patch("typix_store.remote.load_config", return_value=self.config), patch("typix_store.remote.GitHubSource", return_value=remote):
            self.assertEqual(client.load_catalog()[0].package, "typix-reader")
        self.assertIsNone(client.remote)
        self.assertIn("尚未发布", client.source_notice)

    def test_fd_identity_change_after_lstat_is_rejected(self):
        source = self.root / "racing"
        source.write_bytes(b"safe")
        original_open = os.open
        def changed_open(path, flags):
            source.rename(self.root / "old")
            source.write_bytes(b"evil")
            return original_open(path, flags)
        with patch("typix_store.staging.os.open", side_effect=changed_open), self.assertRaisesRegex(CatalogError, "发生变化"):
            copy_user_file(source, self.root / "copy", os.getuid(), 4)

    def test_staging_cancel_never_signals_privileged_helper(self):
        import subprocess
        from typix_store.client import StoreClient
        client = StoreClient()
        client.remote = Mock(catalog=self.root / "catalog.json", signature=self.root / "catalog.json.sig")
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("stage", 0.2), ("", "")]
        with patch("typix_store.client.subprocess.Popen", return_value=process), self.assertRaisesRegex(CatalogError, "尚未开始安装"):
            client.stage_remote(self.app, self.root / "package.deb", lambda: True)
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_small_fetch_checks_deadline_after_one_network_read(self):
        response = Response(b"abc")
        response.read = Mock(side_effect=AssertionError("buffer-filling read must not be used"))
        original_read1 = response.read1
        response.read1 = Mock(side_effect=lambda size: original_read1(1))
        with patch("typix_store.remote.time.monotonic", side_effect=[0, 0, 31]), self.assertRaisesRegex(CatalogError, "超时"):
            fetch_small(self.config.asset_url("catalog.json"), 20, opener=lambda *_: response)
        response.read1.assert_called_once()

    def test_download_checks_cancel_after_one_network_read(self):
        response = Response(self.package)
        response.read = Mock(side_effect=AssertionError("buffer-filling read must not be used"))
        original_read1 = response.read1
        response.read1 = Mock(side_effect=lambda size: original_read1(1))
        self.source.opener = lambda *_: response
        cancelled = Mock(side_effect=[False, True])
        with self.assertRaisesRegex(CatalogError, "取消"):
            self.source.download(self.app, cancelled=cancelled)
        response.read1.assert_called_once()
        partial = next((self.source.cache / "downloads").glob("*.part"))
        self.assertEqual(partial.read_bytes(), self.package[:1])

    def test_stage_fd_rejects_symlink_fifo_owner_and_size(self):
        source = self.root / "input"
        source.write_bytes(b"safe")
        destination = self.root / "copied"
        copy_user_file(source, destination, os.getuid(), 4)
        self.assertEqual(destination.read_bytes(), b"safe")
        destination.unlink()
        with self.assertRaises(CatalogError):
            copy_user_file(source, destination, os.getuid() + 1, 4)
        with self.assertRaises(CatalogError):
            copy_user_file(source, destination, os.getuid(), 3)
        link = self.root / "link"
        link.symlink_to(source)
        with self.assertRaises(CatalogError):
            copy_user_file(link, destination, os.getuid(), 4)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(CatalogError):
            copy_user_file(fifo, destination, os.getuid(), 4)

    def test_compressed_package_expansion_space_is_checked(self):
        raw = copy.deepcopy(self.data)
        raw["applications"][0]["versions"][0]["artifact"]["installedSizeBytes"] = 300 * 1024 * 1024
        contents = json.dumps(raw).encode()
        cat, sig, deb = [self.root / name for name in ("catalog.json", "catalog.json.sig", "reader.deb")]
        cat.write_bytes(contents)
        sig.write_bytes(self.private.sign(contents))
        deb.write_bytes(self.package)
        profile = DeviceProfile("arm64", "raspios-trixie", 2000, 100)
        with self.assertRaisesRegex(CatalogError, "展开"):
            verify_copied(cat, sig, deb, self.app.id, self.config, profile)

    def test_stage_independently_rejects_hash_os_and_wrong_app(self):
        contents = json.dumps(self.data).encode()
        cat, sig, deb = [self.root / filename for filename in ("catalog.json", "catalog.json.sig", "reader.deb")]
        cat.write_bytes(contents)
        sig.write_bytes(self.private.sign(contents))
        deb.write_bytes(self.package)
        profile = DeviceProfile("arm64", "raspios-trixie", 2000, 1024)
        with patch("typix_store.staging.verify_deb_metadata"):
            self.assertEqual(verify_copied(cat, sig, deb, self.app.id, self.config, profile).id, self.app.id)
        for app_id, active_profile in [("wrong-id", profile), (self.app.id, DeviceProfile("amd64", "debian-trixie", 2000, 1024))]:
            with self.assertRaises(CatalogError):
                verify_copied(cat, sig, deb, app_id, self.config, active_profile)
        deb.write_bytes(b"x" * len(self.package))
        with self.assertRaisesRegex(CatalogError, "SHA-256"):
            verify_copied(cat, sig, deb, self.app.id, self.config, profile)
