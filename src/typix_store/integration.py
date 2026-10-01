"""Local desktop integration; never touches package data or custom shortcuts."""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .catalog import CATEGORY_RE, CatalogError

DESKTOP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.desktop")


def category_name(values) -> str:
    if isinstance(values, str):
        values = [value for value in values.split(";") if value]
    if (not isinstance(values, (list, tuple, set, frozenset)) or len(values) > 32
            or any(not isinstance(value, str) or not CATEGORY_RE.fullmatch(value) for value in values)):
        return "应用"
    values = set(values)
    for keys, name in (({"Game", "Emulator"}, "游戏"),
                       ({"AudioVideo", "Audio", "Video", "Player"}, "影音"),
                       ({"Network", "WebBrowser"}, "网络"),
                       ({"Settings", "System"}, "系统"), ({"Utility", "Office"}, "工具")):
        if values & keys:
            return name
    return "应用"


class ShortcutManager:
    def __init__(self, state=None, applications=Path("/usr/share/applications")):
        self.state = state or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "typix-store/shortcuts.json"
        self.applications = applications

    def read(self):
        if not self.state.exists():
            return {}
        try:
            info = self.state.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise ValueError()
            raw = json.loads(self.state.read_bytes())
            if not isinstance(raw, dict) or len(raw) > 100:
                raise ValueError()
            for name, row in raw.items():
                if (not DESKTOP_NAME.fullmatch(name) or not isinstance(row, dict)
                        or set(row) != {"shortcut", "managed", "disabled"}
                        or not isinstance(row["shortcut"], str) or not DESKTOP_NAME.fullmatch(row["shortcut"])
                        or type(row["managed"]) is not bool or type(row["disabled"]) is not bool):
                    raise ValueError()
            return raw
        except (OSError, ValueError, TypeError):
            raise CatalogError("快捷方式偏好无法读取；已保留现有桌面文件") from None

    def save(self, rows):
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".shortcuts-", dir=self.state.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(rows, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.state)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def matches(path, source):
        return path.is_symlink() and path.readlink() == source

    def sync(self, app, desktop, installed):
        if not app.desktop_file or desktop is None:
            return
        name = app.desktop_file
        if not DESKTOP_NAME.fullmatch(name):
            raise CatalogError("应用桌面入口名称无效")
        rows = self.read()
        row = rows.get(name)
        source = self.applications / name
        destination = desktop / (row["shortcut"] if row else name)
        if not installed:
            removed = row and row["managed"] and self.matches(destination, source)
            if removed:
                destination.unlink()
            if row:
                row["managed"] = False
                try:
                    self.save(rows)
                except OSError:
                    if removed and not os.path.lexists(destination):
                        destination.symlink_to(source)
                    raise CatalogError("快捷方式偏好保存失败，已保留管理状态") from None
            return
        if row and row["managed"] and not os.path.lexists(destination):
            row.update(managed=False, disabled=True)
            self.save(rows)  # Respect shortcuts the user deleted outside Store.
            return
        if row and row["disabled"]:
            return
        if os.path.lexists(destination):
            if self.matches(destination, source):
                return
            # A custom shortcut remains intact. Use a distinct managed name.
            destination = desktop / ("store-" + name)
            if os.path.lexists(destination):
                return
        if not source.is_file():
            return
        from .client import require_system_owned
        require_system_owned(source)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(source, encoding="utf-8")
        entry = parser["Desktop Entry"]
        if (entry.get("Type") != "Application" or not entry.get("Exec")
                or entry.getboolean("Hidden", fallback=False) or entry.getboolean("NoDisplay", fallback=False)):
            return
        existing = next((path for path in (desktop, *desktop.parents) if path.exists()), None)
        if existing is None or shutil.disk_usage(existing).free < 1024 * 1024:
            raise CatalogError("桌面可用空间不足，未创建快捷方式")
        desktop.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
        rows[name] = {"shortcut": destination.name, "managed": True, "disabled": False}
        try:
            self.save(rows)
        except OSError:
            if self.matches(destination, source):
                destination.unlink()
            raise CatalogError("快捷方式偏好保存失败，未留下无管理入口") from None
