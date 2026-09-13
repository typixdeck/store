"""Narrow pkexec helper: verify a submitted complete deb, never install it.

All input bytes are copied from unprivileged-owned regular file descriptors
before parsing. Trust and destination always come from root-owned system paths.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import stat
import sys
import syslog
import tempfile
from pathlib import Path

from .catalog import CatalogError, DeviceProfile, incompatibility_reasons, load_catalog_file, verify_deb_metadata, verify_hash
from .client import require_system_owned
from .remote import CONFIG_PATH, MAX_CATALOG, MAX_PACKAGE, load_config

VERIFIED_DIR = Path("/var/cache/typix-store/verified")
MAX_VERIFIED = 2 * MAX_PACKAGE


def copy_user_file(source: Path, destination: Path, uid: int, limit: int):
    initial = source.lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_uid != uid or initial.st_size > limit or initial.st_size <= 0:
        raise CatalogError("暂存输入必须是当前用户所有、大小受限的普通文件")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(source, flags)
    try:
        details = os.fstat(descriptor)
        if (details.st_dev, details.st_ino) != (initial.st_dev, initial.st_ino):
            raise CatalogError("输入文件在打开时发生变化")
        if not stat.S_ISREG(details.st_mode) or details.st_uid != uid or details.st_size > limit or details.st_size <= 0:
            raise CatalogError("暂存输入必须是当前用户所有、大小受限的普通文件")
        with os.fdopen(descriptor, "rb", closefd=False) as source_stream, destination.open("xb") as output:
            total = 0
            while True:
                block = source_stream.read(min(262144, limit - total + 1))
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise CatalogError("暂存输入超过大小上限")
                output.write(block)
            if total != details.st_size:
                raise CatalogError("输入文件在复制时发生变化")
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def verify_copied(catalog, signature, package, app_id, config, profile):
    apps = load_catalog_file(catalog, signature=signature, public_key=config.public_key,
                            expected_repository=config.repository, expected_channel=config.channel)
    app = next((candidate for candidate in apps if candidate.id == app_id), None)
    if app is None:
        raise CatalogError("签名目录不存在所选应用")
    record = app.current_record()
    # pkexec intentionally strips GUI environment. The GUI has checked display
    # and input capabilities; privileged staging independently enforces arch,
    # official OS, RAM and storage, without guessing session display capability.
    reasons = incompatibility_reasons({**record, "compatibility": {**record["compatibility"], "display": [], "requiredFeatures": []}}, profile)
    if reasons:
        raise CatalogError("；".join(reasons))
    artifact = record["artifact"]
    if profile.free_disk_mb * 1024 * 1024 < artifact["installedSizeBytes"] + 64 * 1024 * 1024:
        raise CatalogError("系统空间不足以展开完整软件包，尚未开始安装")
    if artifact["sizeBytes"] > MAX_PACKAGE or package.stat().st_size != artifact["sizeBytes"]:
        raise CatalogError("暂存包大小与签名目录不一致")
    verify_hash(package, artifact["sha256"])
    verify_deb_metadata(package, app)
    return app


def stage(catalog, signature, package, app_id, uid):
    config = load_config(CONFIG_PATH)
    # Check each existing ancestor before creating any privileged directory.
    require_system_owned(VERIFIED_DIR.parent.parent)
    for directory in (VERIFIED_DIR.parent, VERIFIED_DIR):
        if os.path.lexists(directory):
            require_system_owned(directory)
        else:
            directory.mkdir(mode=0o755)
            require_system_owned(directory)
    lock_path = VERIFIED_DIR / ".stage.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        require_system_owned(lock_path)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != 0:
            raise CatalogError("系统暂存锁异常")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CatalogError("已有软件包正在安全暂存，请稍后重试") from exc
        return _stage_locked(catalog, signature, package, app_id, uid, config)
    finally:
        os.close(descriptor)


def _stage_locked(catalog, signature, package, app_id, uid, config):
    usage = sum(item.stat().st_size for item in VERIFIED_DIR.iterdir() if item.is_file() and not item.is_symlink())
    if usage + min(package.stat().st_size, MAX_PACKAGE) > MAX_VERIFIED:
        raise CatalogError("系统暂存缓存已满，请管理员清理已完成的缓存后重试")
    if shutil.disk_usage(VERIFIED_DIR).free < MAX_CATALOG + 64 * 1024 * 1024 + min(package.stat().st_size, MAX_PACKAGE):
        raise CatalogError("系统暂存空间不足，尚未开始安装")
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=VERIFIED_DIR) as temporary:
        directory = Path(temporary)
        copied_catalog, copied_sig, copied_deb = directory / "catalog.json", directory / "catalog.json.sig", directory / "package.deb"
        copy_user_file(catalog, copied_catalog, uid, MAX_CATALOG)
        copy_user_file(signature, copied_sig, uid, 64)
        # Authenticate the catalog before allowing a large package copy.
        apps = load_catalog_file(copied_catalog, signature=copied_sig, public_key=config.public_key,
                                 expected_repository=config.repository, expected_channel=config.channel)
        selected = next((app for app in apps if app.id == app_id), None)
        if selected is None or selected.current_record()["artifact"]["sizeBytes"] > MAX_PACKAGE:
            raise CatalogError("未找到大小受限的已签名完整软件包")
        copy_user_file(package, copied_deb, uid, selected.current_record()["artifact"]["sizeBytes"])
        app = verify_copied(copied_catalog, copied_sig, copied_deb, app_id, config, DeviceProfile.from_environment())
        target = VERIFIED_DIR / (app.current_record()["artifact"]["sha256"].lower() + ".deb")
        if os.path.lexists(target):
            require_system_owned(target)
        copied_deb.chmod(0o644)
        os.replace(copied_deb, target)
        syslog.openlog("typix-store-stage")
        syslog.syslog(syslog.LOG_NOTICE, f"verified staging uid={uid} package={app.package} version={app.current_version} sha256={target.stem}")
        return target


def main():
    parser = argparse.ArgumentParser(description="Verify and stage one signed Typix Store complete package")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--app-id", required=True)
    args = parser.parse_args()
    try:
        uid = int(os.environ.get("PKEXEC_UID", "0"))
        if os.geteuid() != 0 or uid <= 0:
            raise CatalogError("暂存必须通过当前用户的 pkexec 系统授权")
        print(stage(args.catalog, args.signature, args.package, args.app_id, uid))
        return 0
    except (OSError, ValueError) as exc:
        syslog.openlog("typix-store-stage")
        syslog.syslog(syslog.LOG_WARNING, "staging rejected")
        print(f"软件包暂存失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
