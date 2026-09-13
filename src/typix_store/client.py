"""Offline repository operations; called by workers, never by GTK event handlers."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

from .catalog import CatalogError, DeviceProfile, StoreApp, incompatibility_reasons, load_catalog_file, verify_deb_metadata, verify_hash

DEFAULT_REPOSITORY = Path("/usr/share/typix-store/repository")
DEFAULT_PUBLIC_KEY = Path("/usr/share/typix-store/keys/development.pem")
PROTECTED_PACKAGES = frozenset({"typix-launcher", "typix-store"})


def require_system_owned(path: Path) -> None:
    """Prevent a local replacement between verification and PackageKit opening it."""
    for item in (path, *path.parents):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o022:
            raise CatalogError("安装源必须由 root 管理且不可由普通用户改写；当前目录仅可浏览")


class StoreClient:
    def __init__(self, repository: Path = DEFAULT_REPOSITORY, public_key: Path = DEFAULT_PUBLIC_KEY, github_config: Path | None = Path("/etc/typix-store/github.json")):
        self.repository = repository
        self.public_key = public_key
        self.github_config = github_config
        self.remote = None
        self.source_notice = "离线应用源；尚未配置 GitHub 完整软件包下载"
        self.source_title = "离线应用源"

    def load_catalog(self, cancelled=lambda: False) -> list[StoreApp]:
        self.remote = None
        if self.github_config is not None and self.github_config.exists():
            from .remote import GitHubSource, load_config
            config = load_config(self.github_config)
            remote = GitHubSource(config)
            try:
                apps = remote.load(cancelled)
                self.remote = remote
                self.source_title = "GitHub 完整应用"
                self.source_notice = remote.notice
                return apps
            except CatalogError:
                if cancelled():
                    raise
                self.source_notice = "GitHub 尚未发布签名目录，当前显示本机离线应用；发布后可刷新" if remote.failure_kind == "unpublished" else "GitHub 暂不可用，已回到本机离线应用源；联网后可刷新重试"
        else:
            self.source_notice = "尚未配置 GitHub 仓库；当前仅浏览本机离线应用源"
        self.source_title = "离线应用源"
        return load_catalog_file(self.repository / "catalog.json", signature=self.repository / "catalog.json.sig", public_key=self.public_key)

    def installation_source_error(self) -> str | None:
        """Read-only capability state for the UI; transaction checks still rerun.

        A valid development signature permits browsing. Only administrator-owned
        sources can supply privileged transactions, even when the bytes match.
        """
        if self.remote is not None:
            try:
                require_system_owned(Path("/usr/libexec/typix-store-stage"))
                if not shutil.which("pkexec"):
                    return "缺少系统授权组件 pkexec，请管理员安装 Store 0.3"
            except (CatalogError, OSError):
                return "系统暂存组件尚未部署，GitHub 应用仅可浏览"
            return None
        try:
            for path in (self.public_key, self.repository / "catalog.json",
                         self.repository / "catalog.json.sig", self.repository / "packages"):
                require_system_owned(path)
            if not (self.repository / "packages").is_dir():
                return "系统安装源不完整，等待管理员部署"
        except CatalogError:
            return "用户目录预览，系统安装待管理员部署"
        except OSError:
            return "系统安装源不完整，等待管理员部署"
        return None

    def trusted_app(self, selected: StoreApp) -> StoreApp:
        # Revalidate expiry/signature for each transaction; stale UI state grants nothing.
        apps = self.remote.verify(self.remote.catalog, self.remote.signature) if self.remote else load_catalog_file(self.repository / "catalog.json", signature=self.repository / "catalog.json.sig", public_key=self.public_key)
        current = next((app for app in apps if app.id == selected.id), None)
        if current is None or current.raw != selected.raw:
            raise CatalogError("目录已经变化，请刷新后重试")
        return current

    def prepare_install(self, selected: StoreApp, cancelled=lambda: False, profile: DeviceProfile | None = None, progress=lambda *_: None) -> Path:
        app = self.trusted_app(selected)
        profile = profile or DeviceProfile.from_environment()
        reasons = incompatibility_reasons(app.current_record(), profile)
        if reasons:
            raise CatalogError("；".join(reasons))
        artifact = app.current_record()["artifact"]
        if self.remote is not None:
            downloaded = self.remote.download(app, cancelled, progress)
            verify_deb_metadata(downloaded, app)
            needed = artifact["installedSizeBytes"] + artifact["sizeBytes"] + 64 * 1024 * 1024
            if shutil.disk_usage("/").free < needed:
                raise CatalogError("下载完成，但系统空间不足以暂存并展开应用；释放空间后重试")
            self.check_package_database()
            if cancelled():
                raise CatalogError("操作已取消，尚未开始系统暂存或安装")
            progress(-1, 0)
            path = self.stage_remote(app, downloaded, cancelled)
            require_system_owned(path)
            verify_hash(path, artifact["sha256"], cancelled)
            verify_deb_metadata(path, app)
            if shutil.disk_usage("/").free < artifact["installedSizeBytes"] + 64 * 1024 * 1024:
                raise CatalogError("暂存后系统空间不足以展开应用，尚未开始安装")
            if cancelled():
                raise CatalogError("操作已取消，暂存文件已验证但尚未安装")
            return path
        path = self.repository / "packages" / artifact["filename"]
        require_system_owned(self.public_key)
        require_system_owned(self.repository / "catalog.json")
        require_system_owned(self.repository / "catalog.json.sig")
        require_system_owned(path)
        if not path.is_file() or path.stat().st_size != artifact["sizeBytes"]:
            raise CatalogError("离线软件包缺失或大小不符，请重新部署可信仓库")
        needed = max(64 * 1024 * 1024, artifact["sizeBytes"] * 3, int(artifact.get("installedSizeBytes", 0)))
        if shutil.disk_usage("/").free < needed:
            raise CatalogError("系统可用空间不足，尚未开始安装；释放空间后重试")
        if cancelled():
            raise CatalogError("操作已取消，尚未修改软件包")
        verify_hash(path, artifact["sha256"], cancelled)
        verify_deb_metadata(path, app)
        self.check_package_database()
        if cancelled():
            raise CatalogError("操作已取消，尚未修改软件包")
        return path

    def stage_remote(self, app, downloaded, cancelled):
        # pkexec performs a bounded copy/verification only. It never starts dpkg;
        # a cancellation or denied prompt cannot start a package transaction.
        command = ["pkexec", "/usr/libexec/typix-store-stage", "--catalog", str(self.remote.catalog),
                   "--signature", str(self.remote.signature), "--package", str(downloaded), "--app-id", app.id]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 180
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled() or time.monotonic() > deadline:
                    # A root staging process may finish its bounded validation;
                    # never signal it, and never start PackageKit after cancel.
                    threading.Thread(target=process.communicate, daemon=True, name="typix-stage-reaper").start()
                    raise CatalogError("系统暂存等待已取消或超时，尚未开始安装")
        if process.returncode != 0:
            raise CatalogError("系统暂存被取消、拒绝或校验失败：" + (stderr.strip()[:700] or "请在设备上完成管理员授权"))
        expected = Path("/var/cache/typix-store/verified") / (app.current_record()["artifact"]["sha256"].lower() + ".deb")
        if stdout.strip() != str(expected) or cancelled():
            raise CatalogError("暂存已取消或返回了异常路径，尚未开始安装")
        return expected

    def prepare_remove(self, selected: StoreApp) -> str:
        app = self.trusted_app(selected)
        if self.remote is not None:
            from .remote import load_config
            config = load_config(self.github_config)
            if config != self.remote.config:
                raise CatalogError("系统 GitHub 配置已变化，请刷新")
        else:
            require_system_owned(self.public_key)
            require_system_owned(self.repository / "catalog.json")
            require_system_owned(self.repository / "catalog.json.sig")
        if app.package in PROTECTED_PACKAGES:
            raise CatalogError("Launcher 和 Store 是系统入口，Alpha 不提供移除操作")
        if not self.installed_version(app):
            raise CatalogError("应用尚未安装，请刷新列表")
        self.check_package_database()
        if shutil.disk_usage("/").free < 8 * 1024 * 1024:
            raise CatalogError("剩余空间不足以安全更新软件包数据库，请先释放空间")
        return app.package

    def installed_version(self, app: StoreApp) -> str | None:
        # Query only this signed catalog entry, do not collect the system inventory.
        try:
            result = subprocess.run(["dpkg-query", "--show", "--showformat=${Status}\n${Version}", app.package], capture_output=True, text=True, timeout=10)
        except OSError:
            return None
        except subprocess.TimeoutExpired as exc:
            raise CatalogError("软件包状态查询超时，请稍后刷新") from exc
        lines = result.stdout.strip().splitlines()
        return lines[1] if result.returncode == 0 and len(lines) == 2 and lines[0] == "install ok installed" else None

    @staticmethod
    def check_package_database() -> None:
        try:
            result = subprocess.run(["dpkg", "--audit"], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CatalogError("无法确认软件包数据库状态，请稍后重试") from exc
        if result.returncode != 0 or result.stdout.strip():
            raise CatalogError("系统存在未完成的软件包事务；请由管理员检查 dpkg 状态，修复后点击刷新")

    @staticmethod
    def has_update(installed: str | None, available: str) -> bool:
        if installed is None:
            return False
        try:
            result = subprocess.run(["dpkg", "--compare-versions", available, "gt", installed], capture_output=True, timeout=5)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def desktop_directory() -> Path:
        try:
            result = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
            value = result.stdout.strip()
            if result.returncode == 0 and value and Path(value).is_absolute():
                path = Path(value)
                if path == Path.home():
                    raise CatalogError("系统桌面目录已禁用，请先在系统中启用桌面目录")
                return path
        except (OSError, subprocess.TimeoutExpired):
            pass
        return Path.home() / "Desktop"

    @staticmethod
    def shortcut_exists(app: StoreApp, desktop: Path) -> bool:
        return bool(app.desktop_file and os.path.lexists(desktop / app.desktop_file))

    def set_shortcut(self, selected: StoreApp, desktop: Path, enabled: bool) -> None:
        app = self.trusted_app(selected)
        if not app.desktop_file:
            raise CatalogError("此应用没有标准桌面入口")
        source = Path("/usr/share/applications") / app.desktop_file
        destination = desktop / app.desktop_file
        if enabled:
            if not self.installed_version(app) or not source.is_file():
                raise CatalogError("请先安装应用，再添加桌面快捷方式")
            require_system_owned(source)
            if os.path.lexists(destination):
                raise CatalogError("桌面已有同名快捷方式，已保留原文件")
            desktop.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(desktop).free < 1024 * 1024:
                raise CatalogError("桌面可用空间不足，未创建快捷方式")
            # Symlink to the package-owned entry stays current across upgrades.
            destination.symlink_to(source)
        else:
            if not os.path.lexists(destination):
                return
            if destination.is_symlink() and destination.resolve() == source.resolve():
                destination.unlink()
            elif destination.is_file() and source.is_file() and destination.read_bytes() == source.read_bytes():
                destination.unlink()
            else:
                raise CatalogError("此快捷方式已被自定义，请通过文件管理器移除")
