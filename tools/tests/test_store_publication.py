import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from store_publication import (
    DEFAULT_SOURCE, ROOT, PublicationError, _bounded_output, catalog_bytes, inspect_packages,
    load_key, package_fields, read_payload, sha256_file, write_publication,
)


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required for real package validation")
class CompleteDebPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="typix-publication-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "debs"
        self.source.mkdir()
        self.stage = self.root / "package"
        self.stage.mkdir()
        (self.stage / "DEBIAN").mkdir()
        self.control = (
            "Package: typix-test\nVersion: 1.0.0-1\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nDepends: python3 (>= 3.11)\n"
            "X-Typix-Compatible-OS: raspios-bookworm,raspios-trixie\n"
            "Description: Local publication test fixture\n"
        )
        self.filename = "typix-test_1.0.0-1_all.deb"
        self.write_file("usr/bin/typix-test", '#!/bin/sh\nexec /usr/bin/python3 -m typix_test "$@"\n', 0o755)
        self.write_file("usr/share/applications/typix-test.desktop", "[Desktop Entry]\nType=Application\nName=Test\nExec=/usr/bin/typix-test %f\n")
        for filename, body in (("__init__.py", '"""Application package."""\n'),
                               ("__main__.py", "from .app import run\nrun()\n"),
                               ("app.py", "def run():\n    return 42\n")):
            self.write_file("usr/lib/python3/dist-packages/typix_test/" + filename, body)
        self.metadata = {
            "id": "ai.typixdeck.test", "package": "typix-test", "filename": self.filename,
            "name": {"en": "Test"}, "summary": {"en": "Full fixture"}, "description": {"en": "Tests package integrity"},
            "desktopFile": "typix-test.desktop", "categories": ["Office"],
            "runtime": {"kind": "python-module", "module": "typix_test", "root": "/usr/lib/python3/dist-packages"},
            "requiredPayload": ["/usr/bin/typix-test", "/usr/lib/python3/dist-packages/typix_test/__init__.py",
                                "/usr/lib/python3/dist-packages/typix_test/__main__.py", "/usr/lib/python3/dist-packages/typix_test/app.py"],
            "compatibility": {"arch": ["arm64"], "minMemoryMB": 128, "minFreeDiskMB": 64,
                              "display": ["wayland", "x11"], "requiredFeatures": []},
        }
        self.build()

    def write_file(self, name, data, mode=0o644):
        path = self.stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        path.chmod(mode)

    def write_manifest(self):
        (self.source / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "applications": [self.metadata]}))

    def build(self):
        (self.stage / "DEBIAN/control").write_text(self.control)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(self.stage), str(self.source / self.filename)], check=True, capture_output=True)
        self.metadata["sha256"] = sha256_file(self.source / self.filename)
        self.write_manifest()

    def test_complete_deb_metadata_and_original_bytes_survive_release_publication(self):
        private = self.root / "signing/private.pem"
        key = load_key(private, create=True)
        output = self.root / "release"
        hashes = write_publication(self.source, output, key, channel="github-release", repository="test-owner/fixture")
        self.assertEqual((output / self.filename).read_bytes(), (self.source / self.filename).read_bytes())
        raw = (output / "catalog.json").read_bytes()
        public = serialization.load_pem_public_key((output / "public-key.pem").read_bytes())
        public.verify((output / "catalog.json.sig").read_bytes(), raw)
        catalog = json.loads(raw)
        artifact = catalog["applications"][0]["versions"][0]["artifact"]
        self.assertEqual(artifact["payload"], "complete-deb")
        self.assertEqual(artifact["depends"], "python3 (>= 3.11)")
        self.assertGreater(artifact["installedSizeBytes"], 0)
        self.assertEqual(artifact["sha256"], hashes[self.filename])
        self.assertNotIn("url", artifact)
        self.assertEqual(catalog["repository"], "test-owner/fixture")
        self.assertNotIn("PRIVATE KEY", "".join(path.read_text(errors="ignore") for path in output.iterdir()))
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(InvalidSignature):
            public.verify((output / "catalog.json.sig").read_bytes(), raw + b"\n")

    def test_raw_channel_can_write_back_beside_original_debs(self):
        key = load_key(self.root / "key.pem", create=True)
        before = (self.source / self.filename).read_bytes()
        write_publication(self.source, self.source, key, channel="github-repository", repository="test-owner/fixture")
        self.assertEqual((self.source / self.filename).read_bytes(), before)
        self.assertEqual(json.loads((self.source / "catalog.json").read_bytes())["channel"], "github-repository")

    def test_package_hash_mismatch_is_rejected_before_publication(self):
        with (self.source / self.filename).open("ab") as output:
            output.write(b"tamper")
        with self.assertRaisesRegex(PublicationError, "SHA-256"):
            inspect_packages(self.source)

    def test_missing_runtime_code_is_not_a_complete_application(self):
        (self.stage / "usr/lib/python3/dist-packages/typix_test/app.py").unlink()
        self.build()
        with self.assertRaisesRegex(PublicationError, "missing"):
            inspect_packages(self.source)

    def test_wrapper_cannot_point_to_undeployed_opt_application(self):
        self.write_file("usr/bin/typix-test", '#!/bin/sh\nexec /opt/myai/myai_flutter_client "$@"\n', 0o755)
        self.build()
        with self.assertRaisesRegex(PublicationError, "outside"):
            inspect_packages(self.source)

    def test_isolated_python_launcher_keeps_declared_package_validation(self):
        self.write_file("usr/bin/typix-test", '#!/bin/sh\nexec /usr/bin/python3 -I -m typix_test "$@"\n', 0o755)
        self.build()
        self.assertEqual(inspect_packages(self.source)[0]["package"], "typix-test")

    def test_isolated_python_does_not_allow_other_entrypoints_or_flags(self):
        for args in ['-I -m other_app', '-I -c "import typix_test"', '-I -S -m typix_test']:
            with self.subTest(args=args):
                self.write_file("usr/bin/typix-test", '#!/bin/sh\nexec /usr/bin/python3 ' + args + ' "$@"\n', 0o755)
                self.build()
                with self.assertRaisesRegex(PublicationError, "outside"):
                    inspect_packages(self.source)

    def test_desktop_exec_must_exist_and_be_executable_in_package(self):
        self.write_file("usr/share/applications/typix-test.desktop", "[Desktop Entry]\nType=Application\nName=Test\nExec=/opt/missing-app\n")
        self.build()
        with self.assertRaises(PublicationError):
            inspect_packages(self.source)

    def test_required_payload_symlink_does_not_count_as_application_code(self):
        code = self.stage / "usr/lib/python3/dist-packages/typix_test/app.py"
        code.unlink()
        code.symlink_to("/opt/missing.py")
        self.build()
        with self.assertRaisesRegex(PublicationError, "regular file"):
            inspect_packages(self.source)

    def test_control_requires_depends_and_explicit_os_compatibility(self):
        original = self.control
        for field in ("Depends:", "X-Typix-Compatible-OS:"):
            with self.subTest(field=field):
                self.control = "\n".join(line for line in original.splitlines() if not line.startswith(field)) + "\n"
                self.build()
                with self.assertRaisesRegex(PublicationError, "missing"):
                    inspect_packages(self.source)

    def test_package_filename_and_manifest_cannot_conflict_with_control(self):
        self.control = self.control.replace("Version: 1.0.0-1", "Version: 2.0.0-1")
        self.build()
        with self.assertRaisesRegex(PublicationError, "conflicts"):
            inspect_packages(self.source)

    def test_extra_unreviewed_deb_is_not_silently_included(self):
        shutil.copyfile(self.source / self.filename, self.source / "unreviewed_1_all.deb")
        with self.assertRaisesRegex(PublicationError, "Unreviewed"):
            inspect_packages(self.source)

    def test_corrupt_deb_is_rejected_even_if_manifest_hash_matches(self):
        (self.source / self.filename).write_bytes(b"not a deb")
        self.metadata["sha256"] = sha256_file(self.source / self.filename)
        self.write_manifest()
        with self.assertRaisesRegex(PublicationError, "invalid deb"):
            inspect_packages(self.source)

    def test_publisher_requires_actual_repository_and_valid_expiry(self):
        entries = inspect_packages(self.source)
        for repository in (None, "https://github.com/test/repo", "test/repo.git", "../../repo"):
            with self.subTest(repository=repository), self.assertRaises(PublicationError):
                catalog_bytes(entries, channel="github-release", repository=repository)
        with self.assertRaises(PublicationError):
            catalog_bytes(entries, channel="development-offline", days=0)

    def test_private_key_inside_repository_is_refused(self):
        with self.assertRaisesRegex(PublicationError, "outside"):
            load_key(ROOT / "apps/store/debs/must-not-create.pem", create=True)
        self.assertFalse((ROOT / "apps/store/debs/must-not-create.pem").exists())

    def test_actual_submission_directory_contains_complete_packages(self):
        entries = inspect_packages(DEFAULT_SOURCE)
        self.assertTrue(entries)
        # MyAI 0.3 replaced the old entry-only wrapper with a complete native app.
        myai = next((entry for entry in entries if entry["package"] == "typix-myai"), None)
        if myai is not None:
            self.assertEqual(myai["versions"][0]["artifact"]["arch"], "arm64")
            metadata = json.loads((DEFAULT_SOURCE / "manifest.json").read_text())
            submission = next(app for app in metadata["applications"] if app["package"] == "typix-myai")
            self.assertEqual(submission["runtime"]["kind"], "native")
        self.assertTrue(all(entry["versions"][0]["artifact"]["payload"] == "complete-deb" for entry in entries))

    def test_slow_pipe_has_a_wall_deadline_during_iteration(self):
        real_popen = subprocess.Popen

        def slow_process(_command, **kwargs):
            return real_popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

        started = time.monotonic()
        with patch("store_publication.subprocess.Popen", side_effect=slow_process), self.assertRaisesRegex(PublicationError, "wall deadline"):
            read_payload(self.source / self.filename, {"usr/bin/typix-test"}, timeout=0.15)
        self.assertLess(time.monotonic() - started, 2)

    def test_oversized_pax_header_is_refused_before_reading_its_body(self):
        real_popen = subprocess.Popen
        for kind in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                     tarfile.GNUTYPE_LONGLINK, getattr(tarfile, "SOLARIS_XHDTYPE", b"X")):
            with self.subTest(kind=kind):
                header = tarfile.TarInfo("oversized-metadata")
                header.type = kind
                header.size = 1024 * 1024 * 1024
                raw = header.tobuf(format=tarfile.PAX_FORMAT)

                def forged_process(_command, **kwargs):
                    code = "import sys; sys.stdout.buffer.write(bytes.fromhex(" + repr(raw.hex()) + "))"
                    return real_popen([sys.executable, "-c", code], **kwargs)

                with patch("store_publication.subprocess.Popen", side_effect=forged_process), \
                        patch.object(tarfile.TarInfo, "_proc_pax", side_effect=AssertionError("PAX body parser must not run")), \
                        patch.object(tarfile.TarInfo, "_proc_gnulong", side_effect=AssertionError("GNU body parser must not run")), \
                        self.assertRaisesRegex(PublicationError, "special header"):
                    read_payload(self.source / self.filename, {"usr/bin/typix-test"})

    def test_small_valid_pax_header_still_loads_required_file(self):
        output = io.BytesIO()
        name = "usr/share/typix-test/" + "readable-" * 15 + ".txt"
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo(name)
            member.size = 3
            archive.addfile(member, io.BytesIO(b"app"))
        raw = output.getvalue()
        real_popen = subprocess.Popen

        def valid_process(_command, **kwargs):
            code = "import sys; sys.stdout.buffer.write(bytes.fromhex(" + repr(raw.hex()) + "))"
            return real_popen([sys.executable, "-c", code], **kwargs)

        with patch("store_publication.subprocess.Popen", side_effect=valid_process):
            files, size = read_payload(self.source / self.filename, {name})
        self.assertEqual(files[name].data, b"app")
        self.assertEqual(size, 3)

    def test_control_stdout_is_bounded_before_capture(self):
        with self.assertRaisesRegex(PublicationError, "metadata budget"):
            _bounded_output([sys.executable, "-c", "import sys; sys.stdout.write('x' * 1048576)"])

    def test_installed_size_comes_from_actual_payload_not_control_claim(self):
        self.control += "Installed-Size: 1\n"
        self.write_file("usr/share/typix-test/data.dat", "x" * (2 * 1024 * 1024))
        self.build()
        record = inspect_packages(self.source)[0]["versions"][0]
        size = record["artifact"]["installedSizeBytes"]
        self.assertGreater(size, 2 * 1024 * 1024)
        self.assertGreaterEqual(record["compatibility"]["minFreeDiskMB"], (size + 1024 * 1024 - 1) // (1024 * 1024) + 64)


if __name__ == "__main__":
    unittest.main()
