"""Validated, signed offline catalogs and runtime capability checks."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KNOWN_FEATURES = {"wayland", "x11", "touch", "keyboard", "audio", "network", "opengl-es-3", "vulkan", "nvme", "usb-serial"}
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
DEB_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+~:-]*$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_~:-]*\.deb$")
OS_RE = re.compile(r"^[a-z0-9]+-[a-z0-9]+$")


class CatalogError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def _release(path: Path) -> dict[str, str]:
    try:
        return {key: value.strip().strip('\"') for line in path.read_text().splitlines()
                if not line.startswith("#") and "=" in line
                for key, value in [line.split("=", 1)]}
    except OSError:
        return {}


@dataclass(frozen=True)
class DeviceProfile:
    arch: str = "unknown"
    os: str = "unknown"
    memory_mb: int = 0
    free_disk_mb: int = 0
    displays: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()

    @classmethod
    def from_environment(cls) -> "DeviceProfile":
        machine = platform.machine().lower()
        arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "armv7l": "armhf"}.get(machine, machine)
        try:
            result = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                arch = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        release = _release(Path("/etc/os-release"))
        os_id = release.get("ID", "unknown")
        # Raspberry Pi OS arm64 identifies as Debian; its official image marker
        # distinguishes it from arbitrary Debian installations without serial IDs.
        if os_id in {"raspbian", "raspios"} or (os_id == "debian" and Path("/etc/rpi-issue").exists()):
            os_id = "raspios"
        os_name = os_id + "-" + release.get("VERSION_CODENAME", "unknown")
        memory = 0
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    memory = int(line.split()[1]) // 1024
                    break
        except (OSError, ValueError):
            pass
        free = shutil.disk_usage("/").free // (1024 * 1024)
        displays = []
        if os.environ.get("WAYLAND_DISPLAY"):
            displays.append("wayland")
        if os.environ.get("DISPLAY"):
            displays.append("x11")
        features = set(displays)
        try:
            inputs = Path("/proc/bus/input/devices").read_text()
            if "kbd" in inputs:
                features.add("keyboard")
            if "touch" in inputs.lower():
                features.add("touch")
        except OSError:
            pass
        if Path("/dev/snd").is_dir():
            features.add("audio")
        return cls(arch, os_name, memory, free, tuple(displays), frozenset(features))


@dataclass(frozen=True)
class StoreApp:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def package(self) -> str:
        return self.raw["package"]

    @property
    def current_version(self) -> str:
        return self.raw["currentVersion"]

    @property
    def desktop_file(self) -> str | None:
        return self.raw.get("desktopFile")

    def name(self, locale: str = "zh-CN") -> str:
        return localize(self.raw.get("name", self.id), locale)

    def summary(self, locale: str = "zh-CN") -> str:
        return localize(self.raw.get("summary", ""), locale)

    def description(self, locale: str = "zh-CN") -> str:
        return localize(self.raw.get("description", ""), locale)

    def current_record(self) -> dict[str, Any]:
        return next(record for record in self.raw["versions"] if record["version"] == self.current_version)


def localize(value: Any, locale: str) -> str:
    if isinstance(value, dict):
        return str(value.get(locale, value.get("en", value.get("zh-CN", ""))))
    return str(value or "")


def _strings(value: Any, nonempty: bool = False) -> bool:
    return isinstance(value, list) and (bool(value) or not nonempty) and all(isinstance(item, str) and item for item in value)


def validate_catalog(data: Any, now: datetime | None = None, expected_repository: str | None = None, expected_channel: str = "development-offline") -> list[StoreApp]:
    _require(isinstance(data, dict), "catalog 必须是 JSON object")
    _require(data.get("schemaVersion") == 1, "不支持的 catalog schemaVersion")
    _require(data.get("channel") == expected_channel, "应用源频道不匹配")
    if expected_repository is not None:
        _require(data.get("repository") == expected_repository, "签名目录的 GitHub 仓库不匹配")
    try:
        created = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
        expiry = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        current_time = now or datetime.now(timezone.utc)
        _require(created.tzinfo is not None and expiry.tzinfo is not None, "目录时间必须包含时区")
        _require(created <= current_time < expiry, "目录未生效或已过期，请更新离线仓库或校准系统时间")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("目录有效期格式错误") from exc
    _require(isinstance(data.get("applications"), list), "applications 必须是数组")
    apps = []
    seen_ids: set[str] = set()
    seen_packages: set[str] = set()
    for raw in data["applications"]:
        _require(isinstance(raw, dict), "application 必须是 object")
        app_id, package, version = raw.get("id"), raw.get("package"), raw.get("currentVersion")
        _require(isinstance(app_id, str) and bool(app_id) and app_id not in seen_ids, "应用 id 无效或重复")
        _require(isinstance(package, str) and bool(PACKAGE_RE.fullmatch(package)) and package not in seen_packages, "包名无效或重复")
        _require(isinstance(version, str) and bool(DEB_VERSION_RE.fullmatch(version)), "版本格式错误")
        desktop = raw.get("desktopFile")
        _require(desktop is None or (isinstance(desktop, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.desktop", desktop)), "desktopFile 格式错误")
        records = raw.get("versions")
        _require(isinstance(records, list) and all(isinstance(item, dict) for item in records), "versions 格式错误")
        records = [item for item in records if item.get("version") == version]
        _require(len(records) == 1, "缺少唯一 currentVersion 记录")
        artifact, compat = records[0].get("artifact"), records[0].get("compatibility")
        _require(isinstance(artifact, dict) and isinstance(compat, dict), "缺少 artifact 或 compatibility")
        if expected_repository is not None:
            _require(artifact.get("payload") == "complete-deb", "远程应用必须发布完整 deb，不能仅发布入口包装器")
            _require(type(artifact.get("installedSizeBytes")) is int and 0 < artifact["installedSizeBytes"] <= 8 * 1024**3, "远程包必须声明不超过 8 GiB 的实际展开大小")
        _require(isinstance(artifact.get("filename"), str) and bool(FILENAME_RE.fullmatch(artifact["filename"])), "artifact filename 必须是单个 deb 文件名")
        _require(isinstance(artifact.get("sha256"), str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", artifact["sha256"])), "SHA-256 格式错误")
        _require(type(artifact.get("sizeBytes")) is int and artifact["sizeBytes"] > 0, "sizeBytes 格式错误")
        _require(artifact.get("arch") in {"all", "arm64", "armhf", "amd64"}, "包架构格式错误")
        _require(isinstance(artifact.get("depends"), str) or _strings(artifact.get("depends")), "缺少依赖声明")
        _require(_strings(compat.get("arch"), True) and _strings(compat.get("os"), True), "缺少 CPU 或发行版兼容性声明")
        _require(all(OS_RE.fullmatch(item) for item in compat["os"]), "发行版兼容性格式错误")
        _require(_strings(compat.get("display", [])) and _strings(compat.get("requiredFeatures", [])), "能力声明格式错误")
        _require(set(compat.get("requiredFeatures", [])).issubset(KNOWN_FEATURES), "未知 requiredFeatures")
        for field in ("minMemoryMB", "minFreeDiskMB"):
            _require(type(compat.get(field, 0)) is int and compat.get(field, 0) >= 0, f"{field} 格式错误")
        seen_ids.add(app_id)
        seen_packages.add(package)
        apps.append(StoreApp(copy.deepcopy(raw)))
    return apps


def load_catalog_file(path: Path, *, signature: Path | None = None, public_key: Path | None = None, now: datetime | None = None, expected_repository: str | None = None, expected_channel: str = "development-offline") -> list[StoreApp]:
    """Verify exact bytes before JSON parsing; unsigned catalogs never enter the UI."""
    if signature is None or public_key is None:
        raise CatalogError("缺少签名或信任公钥，已拒绝加载目录")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise CatalogError("缺少签名验证组件 python3-cryptography") from exc
    try:
        _require(path.stat().st_size <= 4 * 1024 * 1024, "目录超过大小上限")
        contents = path.read_bytes()
        key = load_pem_public_key(public_key.read_bytes())
        _require(isinstance(key, Ed25519PublicKey), "信任公钥必须是 Ed25519")
        key.verify(signature.read_bytes(), contents)
        return validate_catalog(json.loads(contents), now=now, expected_repository=expected_repository, expected_channel=expected_channel)
    except (OSError, ValueError, ImportError, InvalidSignature) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("目录签名验证或读取失败；请恢复可信离线仓库") from exc


def incompatibility_reasons(record: dict[str, Any], profile: DeviceProfile) -> list[str]:
    compat = record["compatibility"]
    reasons = []
    if profile.arch not in compat["arch"]:
        reasons.append("CPU 架构不匹配：" + profile.arch)
    if profile.os not in compat["os"]:
        reasons.append("系统版本不匹配：" + profile.os)
    if profile.memory_mb < compat.get("minMemoryMB", 0):
        reasons.append(f"至少需要 {compat['minMemoryMB']} MB 内存")
    if profile.free_disk_mb < compat.get("minFreeDiskMB", 0):
        reasons.append(f"至少需要 {compat['minFreeDiskMB']} MB 可用空间")
    if compat.get("display") and not set(compat["display"]).intersection(profile.displays):
        reasons.append("当前显示服务不兼容")
    missing = set(compat.get("requiredFeatures", [])) - profile.features
    if missing:
        reasons.append("未检测到功能：" + ", ".join(sorted(missing)))
    return reasons


def normalize_dependencies(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        value = ",".join(value)
    return sorted(re.sub(r"\s+", "", clause) for clause in value.split(",") if clause.strip())


def verify_deb_metadata(path: Path, app: StoreApp, runner=subprocess.run) -> dict[str, str]:
    names = ["Package", "Version", "Architecture", "Depends", "X-Typix-Compatible-OS"]
    try:
        result = runner(["dpkg-deb", "-f", str(path), *names], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CatalogError(f"无法校验 deb 元数据：{exc}") from exc
    _require(result.returncode == 0, "dpkg-deb 无法读取软件包")
    values = {}
    for line in result.stdout.decode(errors="replace").splitlines():
        key, sep, value = line.partition(":")
        _require(bool(sep), "deb 元数据格式不完整")
        values[key.strip()] = value.strip()
    record = app.current_record()
    artifact = record["artifact"]
    expected = {"Package": app.package, "Version": app.current_version, "Architecture": artifact["arch"]}
    _require(all(values.get(key) == value for key, value in expected.items()), "Registry 元数据与 deb 包字段不一致")
    _require("Depends" in values and normalize_dependencies(values["Depends"]) == normalize_dependencies(artifact["depends"]), "Registry 依赖声明与 deb 不一致")
    actual_os = {item.strip() for item in values.get("X-Typix-Compatible-OS", "").split(",") if item.strip()}
    _require(actual_os == set(record["compatibility"]["os"]), "Registry 发行版兼容性与 deb 不一致")
    return values


def verify_hash(path: Path, expected_sha256: str, cancelled=None) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled and cancelled():
                raise CatalogError("操作已取消，尚未修改软件包")
            digest.update(block)
    _require(digest.hexdigest() == expected_sha256.lower(), "SHA-256 校验失败")
