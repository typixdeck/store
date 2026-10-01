import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from typix_store.catalog import CatalogError, StoreApp
from typix_store.client import StoreClient
from typix_store.integration import ShortcutManager, category_name
from typix_store.icons import icon_file


class ShortcutsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.desktop = self.root / "Desktop"
        self.applications = self.root / "applications"; self.applications.mkdir()
        self.source = self.applications / "typix-test.desktop"
        self.source.write_text("[Desktop Entry]\nType=Application\nName=Test\nExec=/bin/true\nCategories=Office;\n")
        self.app = StoreApp({"package": "typix-test", "desktopFile": self.source.name})
        self.manager = ShortcutManager(self.root / "settings.json", self.applications)
        owned = patch("typix_store.client.require_system_owned"); owned.start(); self.addCleanup(owned.stop)

    def test_install_creates_managed_symlink_update_keeps_current_entry_and_remove_cleans_only_it(self):
        self.manager.sync(self.app, self.desktop, True)
        shortcut = self.desktop / self.source.name
        self.assertEqual(shortcut.readlink(), self.source)
        self.source.write_text(self.source.read_text().replace("/bin/true", "/bin/false"))
        self.manager.sync(self.app, self.desktop, True)
        self.assertIn("/bin/false", shortcut.read_text())
        self.assertEqual(self.manager.state.stat().st_mode & 0o777, 0o600)
        self.manager.sync(self.app, self.desktop, False)
        self.assertFalse(shortcut.exists())

    def test_custom_same_name_is_preserved_and_managed_conflict_entry_is_separate(self):
        self.desktop.mkdir()
        original = self.desktop / self.source.name; original.write_text("CUSTOM FILE")
        self.manager.sync(self.app, self.desktop, True)
        self.assertEqual(original.read_text(), "CUSTOM FILE")
        created = self.desktop / ("store-" + self.source.name)
        self.assertEqual(created.readlink(), self.source)
        self.manager.sync(self.app, self.desktop, False)
        self.assertEqual(original.read_text(), "CUSTOM FILE")
        self.assertFalse(created.exists())

    def test_deleting_managed_shortcut_is_persistent_user_optout(self):
        self.manager.sync(self.app, self.desktop, True)
        (self.desktop / self.source.name).unlink()
        self.manager.sync(self.app, self.desktop, True)
        self.manager.sync(self.app, self.desktop, False)
        self.manager.sync(self.app, self.desktop, True)
        self.assertFalse((self.desktop / self.source.name).exists())
        self.assertTrue(self.manager.read()[self.source.name]["disabled"])

    def test_custom_replacement_and_preexisting_unmanaged_symlink_survive_uninstall(self):
        self.manager.sync(self.app, self.desktop, True)
        path = self.desktop / self.source.name; path.unlink(); path.write_text("CUSTOM")
        self.manager.sync(self.app, self.desktop, False)
        self.assertEqual(path.read_text(), "CUSTOM")
        path.unlink(); path.symlink_to(self.source)
        self.manager.sync(self.app, self.desktop, True)
        self.manager.sync(self.app, self.desktop, False)
        self.assertTrue(path.is_symlink())

    def test_state_save_failure_rolls_back_only_new_shortcut(self):
        with patch.object(self.manager, "save", side_effect=OSError("no space")), self.assertRaises(CatalogError):
            self.manager.sync(self.app, self.desktop, True)
        self.assertFalse(os.path.lexists(self.desktop / self.source.name))

    def test_low_storage_stops_before_creating_shortcut_or_preferences(self):
        with patch("typix_store.integration.shutil.disk_usage", return_value=SimpleNamespace(free=1)), self.assertRaisesRegex(CatalogError, "空间"):
            self.manager.sync(self.app, self.desktop, True)
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.manager.state.exists())

    def test_invalid_preferences_or_missing_source_do_not_touch_desktop(self):
        self.manager.state.write_text("INVALID")
        with self.assertRaises(CatalogError): self.manager.sync(self.app, self.desktop, True)
        self.assertFalse(self.desktop.exists())
        self.manager.state.unlink(); self.source.unlink()
        self.manager.sync(self.app, self.desktop, True)
        self.assertFalse(self.desktop.exists())

    def test_freedesktop_mapping_matches_launcher_precedence(self):
        for values, expected in ((["Game"], "游戏"), (["Office"], "工具"), (["Network", "AudioVideo"], "影音"),
                                 (["Settings"], "系统"), (["WebBrowser"], "网络"), ([], "应用")):
            self.assertEqual(category_name(values), expected)

    def test_malformed_categories_and_icon_have_safe_frontend_fallback(self):
        from typix_store.presentation import icon_name
        for values in (None, 1, True, [["Game"]], [1], {"Game": 1}, ["Game"] * 33):
            with self.subTest(values=values):
                self.assertEqual(category_name(values), "应用")
        self.assertEqual(category_name("Game;Utility;"), "游戏")
        for value in (1, True, [], {}, "/tmp/icon.png", "x" * 129):
            self.assertEqual(icon_name(StoreApp({"package": "typix-reader", "icon": value})), "accessories-text-editor")
        self.assertEqual(icon_name(StoreApp({"package": "typix-reader", "icon": "typix-reader-symbolic"})), "typix-reader-symbolic")


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.client = StoreClient(github_config=None)
        self.app = StoreApp({"package": "typix-test", "desktopFile": "typix-test.desktop"})

    def test_launch_is_independent_of_install_source_and_sends_only_exact_package_entry(self):
        for mode in ("handoff", "resident"):
            with patch.object(self.client, "installed_version", return_value="0.3.1-1"), \
                 patch.object(self.client, "trusted_app", side_effect=CatalogError("expired")), \
                 patch.object(Path, "is_file", return_value=True), \
                 patch("typix_store.client.subprocess.run", return_value=subprocess.CompletedProcess([], 0, mode + "\n")) as run:
                self.assertEqual(self.client.launch(self.app), mode)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/typix-launcher", "--open-installed", "typix-test", "typix-test.desktop"])
            self.assertLessEqual(run.call_args.kwargs["timeout"], 12)

    def test_missing_or_legacy_launcher_never_receives_new_cli_arguments(self):
        with patch.object(self.client, "installed_version", side_effect=["1.0-1", "0.3.0-1"]), \
             patch.object(self.client, "has_update", return_value=False), patch.object(Path, "is_file", return_value=True), \
             patch("typix_store.client.subprocess.run") as run, self.assertRaisesRegex(CatalogError, "更新 Launcher"):
            self.client.launch(self.app)
        run.assert_not_called()

    def test_no_entry_or_not_installed_does_not_launch(self):
        with patch.object(self.client, "installed_version", return_value=None), patch("typix_store.client.subprocess.run") as run, self.assertRaises(CatalogError):
            self.client.launch(self.app)
        run.assert_not_called()

    def test_timeout_and_unacknowledged_handoff_do_not_claim_success(self):
        for response in (subprocess.TimeoutExpired("fixed", 12), subprocess.CompletedProcess([], 0, "")):
            with patch.object(self.client, "installed_version", return_value="0.3.1-1"), patch.object(Path, "is_file", return_value=True), \
                 patch("typix_store.client.subprocess.run", side_effect=response if isinstance(response, Exception) else None,
                       return_value=response if not isinstance(response, Exception) else None), self.assertRaises(CatalogError):
                self.client.launch(self.app)

    def test_current_launcher_single_mode_refusal_asks_to_open_store_from_launcher(self):
        response = subprocess.CompletedProcess([], 1, "", "请先从 Launcher 打开 Store，再启动此应用")
        with patch.object(self.client, "installed_version", return_value="0.3.1-1"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("typix_store.client.subprocess.run", return_value=response), \
             self.assertRaisesRegex(CatalogError, "先从 Launcher 打开 Store") as error:
            self.client.launch(self.app)
        self.assertNotIn("更新", str(error.exception))

    def test_frontend_keeps_installed_launch_enabled_despite_source_or_compatibility_failure(self):
        from typix_store.presentation import actions
        state = actions(installed="1.0-1", update=True, source_error="network unavailable",
                        incompatible=["display not detected"], busy=False, desktop_entry="test.desktop")
        self.assertTrue(state["launch_enabled"])
        self.assertTrue(state["launch_visible"])
        self.assertFalse(state["install_enabled"])
        state = actions(installed=None, update=False, source_error=None, incompatible=[], busy=False, desktop_entry="test.desktop")
        self.assertFalse(state["launch_visible"])
        self.assertTrue(state["install_visible"])


class IconTests(unittest.TestCase):
    def test_assets_are_local_manifest_bound_and_changed_or_traversal_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = b"fixture SVG"; (root / "test.svg").write_bytes(data)
            manifest = {"schema": 1, "icons": {"typix-test": {"filename": "test.svg", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}
            path = root / "manifest.json"; path.write_text(json.dumps(manifest))
            self.assertEqual(icon_file("typix-test", root), root / "test.svg")
            (root / "test.svg").write_bytes(b"changed")
            self.assertIsNone(icon_file("typix-test", root))
            manifest["icons"]["typix-test"]["filename"] = "../other.svg"; path.write_text(json.dumps(manifest))
            self.assertIsNone(icon_file("typix-test", root))
