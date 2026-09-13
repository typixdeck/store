"""Shared complete-deb validation and exact-byte signed catalog publication.

No package installation, maintainer-script execution, network requests or uploads.
"""
from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "debs" if (ROOT / "debs/manifest.json").is_file() else ROOT / "apps/store/debs"
CONTROL_FIELDS = ("Package", "Version", "Architecture", "Depends", "X-Typix-Compatible-OS")
OS_IDS = {"raspios-bookworm", "raspios-trixie"}
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_~:-]*\.deb$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_DEB_BYTES = 512 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_INSPECT_BYTES = 64 * 1024 * 1024
MAX_SPECIAL_HEADER_BYTES = 1024 * 1024
MAX_SPECIAL_HEADERS_TOTAL = 4 * 1024 * 1024


class PublicationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stop_process_group(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # macOS can report EPERM for an already-exited empty process group.
        if process.poll() is None:
            process.kill()


def _bounded_output(command: list[str], *, limit: int = 65536, timeout: float = 30) -> bytes:
    """Bound stdout allocation and elapsed time, including slow child writers."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    length = 0
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, "Control inspection exceeded its wall deadline")
                if not selector.select(remaining):
                    raise PublicationError("Control inspection exceeded its wall deadline")
                chunk = os.read(process.stdout.fileno(), min(65536, limit - length + 1))
                if not chunk:
                    break
                length += len(chunk)
                require(length <= limit, "Control output exceeds the 64 KiB metadata budget")
                chunks.append(chunk)
        require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, "dpkg-deb rejected the package")
        return b"".join(chunks)
    except BaseException:
        _stop_process_group(process)
        raise
    finally:
        if process.poll() is None:
            _stop_process_group(process)
            process.wait()
        if process.stdout:
            process.stdout.close()


def package_fields(path: Path) -> dict[str, str]:
    try:
        output = _bounded_output(["dpkg-deb", "-f", str(path), *CONTROL_FIELDS]).decode("utf-8")
    except (OSError, subprocess.SubprocessError, PublicationError, UnicodeError) as exc:
        raise PublicationError(f"{path.name}: invalid deb control archive: {exc}") from exc
    fields: dict[str, str] = {}
    previous = None
    for line in output.splitlines():
        if line.startswith((" ", "\t")) and previous:
            fields[previous] += " " + line.strip()
            continue
        name, separator, value = line.partition(":")
        require(bool(separator) and name in CONTROL_FIELDS and name not in fields,
                f"{path.name}: duplicate or malformed control field")
        fields[name], previous = value.strip(), name
    require(all(fields.get(name) for name in CONTROL_FIELDS), f"{path.name}: missing required control fields {CONTROL_FIELDS}")
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", fields["Package"])), "Invalid Package field")
    require(bool(re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]*", fields["Version"])), "Invalid Version field")
    require(fields["Architecture"] in {"all", "arm64"}, "Only all/arm64 packages target the official ARM64 image")
    systems = fields["X-Typix-Compatible-OS"].split(",")
    require(all(value.strip() in OS_IDS for value in systems) and len(set(systems)) == len(systems),
            "X-Typix-Compatible-OS must explicitly declare supported official Raspberry Pi OS releases")
    expected_name = f"{fields['Package']}_{fields['Version']}_{fields['Architecture']}.deb"
    require(path.name == expected_name, f"{path.name}: filename conflicts with control metadata ({expected_name})")
    return fields


def payload_path(value: Any) -> str:
    require(isinstance(value, str) and value.startswith("/") and "\\" not in value and "\x00" not in value,
            "Payload paths must be absolute POSIX paths")
    path = PurePosixPath(value)
    require(".." not in path.parts and str(path) == value and value != "/", "Payload path is not canonical")
    return value.lstrip("/")


@dataclass(frozen=True)
class PayloadFile:
    data: bytes
    mode: int
    size: int


def read_payload(path: Path, wanted: set[str], *, timeout: float = 30) -> tuple[dict[str, PayloadFile], int]:
    """Read requested regular files from data.tar; never extract or execute them."""
    files: dict[str, PayloadFile] = {}
    seen: set[str] = set()
    total = 0
    inspected = 0
    process = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(path)], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = threading.Event()

    def terminate() -> None:
        _stop_process_group(process)

    def expire() -> None:
        timed_out.set()
        terminate()

    watchdog = threading.Timer(timeout, expire)
    watchdog.daemon = True
    watchdog.start()

    class BoundedPipe:
        consumed = 0

        def read(self, size):
            require(0 <= size <= 1024 * 1024, "Unbounded tar pipe read refused")
            require(self.consumed + size <= MAX_PAYLOAD_BYTES, "deb tar stream exceeds the total byte budget")
            data = process.stdout.read(size)
            self.consumed += len(data)
            return data

    class BoundedTarInfo(tarfile.TarInfo):
        special_bytes = 0
        headers = 0

        def _proc_member(self, archive):
            cls = type(self)
            cls.headers += 1
            require(cls.headers <= 100000 and 0 <= self.size <= MAX_PAYLOAD_BYTES, "Tar header exceeds inspection limits")
            # Python 3.14 bypasses public frombuf() and calls _frombuf().
            # Guard the common dispatch point instead, before _proc_pax or
            # _proc_gnulong can allocate/read the declared extension body.
            if self.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                             tarfile.GNUTYPE_LONGLINK, getattr(tarfile, "SOLARIS_XHDTYPE", b"X")}:
                cls.special_bytes += self.size
                require(self.size <= MAX_SPECIAL_HEADER_BYTES and cls.special_bytes <= MAX_SPECIAL_HEADERS_TOTAL,
                        "Tar special header exceeds the metadata memory budget")
            return super()._proc_member(archive)

    try:
        assert process.stdout is not None
        with tarfile.open(fileobj=BoundedPipe(), mode="r|", tarinfo=BoundedTarInfo) as archive:
            for member in archive:
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                if name in {"", "."}:
                    continue
                require(not name.startswith("/") and ".." not in PurePosixPath(name).parts and "\\" not in name,
                        "deb data archive contains a traversal path")
                require(name not in seen, f"Duplicate payload member: {name}")
                seen.add(name)
                total += max(0, member.size)
                require(len(seen) <= 100000 and total <= MAX_PAYLOAD_BYTES, "deb expanded payload exceeds inspection limits")
                if name not in wanted:
                    continue
                require(member.isfile() and member.size > 0, f"Required payload is not a nonempty regular file: /{name}")
                inspected += member.size
                require(inspected <= MAX_INSPECT_BYTES, "Required code/assets exceed the 64 MB inspection budget")
                stream = archive.extractfile(member)
                assert stream is not None
                data = stream.read(member.size + 1)
                require(len(data) == member.size, f"Truncated payload file: /{name}")
                files[name] = PayloadFile(data, member.mode, member.size)
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else b""
        require(process.wait(timeout=30) == 0, f"Unable to read deb payload: {stderr.decode(errors='replace')[:200]}")
    except (OSError, tarfile.TarError, subprocess.SubprocessError, PublicationError, RecursionError) as exc:
        terminate()
        process.wait()
        if timed_out.is_set():
            raise PublicationError(f"deb payload inspection exceeded its {timeout:g}-second wall deadline") from exc
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(f"{path.name}: corrupt or unreadable data archive") from exc
    finally:
        watchdog.cancel()
        if process.poll() is None:
            terminate()
            process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    missing = wanted - set(files)
    require(not timed_out.is_set(), "deb payload inspection exceeded its wall deadline")
    require(not missing, "Incomplete application payload; missing: " + ", ".join("/" + value for value in sorted(missing)))
    return files, total


def _launcher_command(data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8")
        commands = [shlex.split(line.strip()) for line in text.splitlines() if line.strip().startswith("exec ")]
    except (UnicodeError, ValueError) as exc:
        raise PublicationError("Cannot inspect launcher command") from exc
    require(len(commands) == 1, "Launcher must have one reviewable exec command")
    return commands[0][1:]


def verify_complete_payload(path: Path, metadata: dict[str, Any]) -> int:
    desktop = metadata.get("desktopFile")
    require(isinstance(desktop, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.desktop", desktop)), "Invalid desktopFile")
    required = metadata.get("requiredPayload")
    require(isinstance(required, list) and bool(required), "requiredPayload must enumerate the actual application code and resources")
    wanted = {payload_path(value) for value in required}
    desktop_path = "usr/share/applications/" + desktop
    wanted.add(desktop_path)
    runtime = metadata.get("runtime")
    require(isinstance(runtime, dict), "runtime must describe the packaged application implementation")
    kind = runtime.get("kind")
    if kind == "python-module":
        module = runtime.get("module")
        require(isinstance(module, str) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module)), "Invalid Python module")
        root = payload_path(runtime.get("root"))
        module_path = root + "/" + module.replace(".", "/")
        implementation = {module_path + "/__init__.py", module_path + "/__main__.py"}
    elif kind in {"python-script", "native"}:
        implementation = {payload_path(runtime.get("path"))}
    else:
        raise PublicationError("Supported reviewed runtimes are python-module, python-script and native")
    require(implementation.issubset(wanted), "requiredPayload omits the runtime implementation")
    files, installed_size = read_payload(path, wanted)
    try:
        desktop_data = configparser.ConfigParser(interpolation=None, strict=True)
        desktop_data.optionxform = str
        desktop_data.read_string(files[desktop_path].data.decode("utf-8"))
        entry = desktop_data["Desktop Entry"]
        command = shlex.split(entry["Exec"])
        require(entry.get("Type") == "Application" and bool(entry.get("Name")), "Desktop entry must name an Application")
        require(command and command[0].startswith("/"), "Desktop Exec must use an absolute packaged executable")
        executable = payload_path(command[0])
    except (UnicodeError, configparser.Error, KeyError, ValueError) as exc:
        raise PublicationError("Invalid .desktop entry or Exec command") from exc
    require(executable in files, "Desktop Exec executable is absent from requiredPayload or the deb")
    require(bool(files[executable].mode & 0o111), "Desktop Exec target is not executable")
    if kind == "native":
        require(executable in implementation, "Native implementation does not match Desktop Exec")
        blob = files[executable].data
        require(len(blob) >= 64 and blob.startswith(b"\x7fELF") and blob[4:6] == b"\x02\x01" and int.from_bytes(blob[18:20], "little") == 183,
                "Native runtime must contain an ARM64 ELF executable")
        require(package_fields(path)["Architecture"] == "arm64", "Native ARM64 code cannot claim Architecture: all")
    else:
        for name, file in files.items():
            if name.endswith(".py"):
                try:
                    require(bool(ast.parse(file.data, filename=name).body), f"Python payload is empty: {name}")
                except (SyntaxError, ValueError) as exc:
                    raise PublicationError(f"Invalid Python implementation: {name}") from exc
        if kind == "python-module":
            expected = ["/usr/bin/python3", "-m", runtime["module"]]
            require(len([name for name in files if name.startswith(module_path + "/") and name.endswith(".py")]) >= 3,
                    "Python module contains entry wrappers but no application implementation")
        else:
            expected = ["/usr/bin/python3", runtime["path"]]
        if executable in implementation and kind == "python-script":
            require(files[executable].data.startswith(b"#!/usr/bin/python3"), "Direct Python executable must declare its interpreter")
        else:
            launcher = _launcher_command(files[executable].data)
            require(launcher[:len(expected)] == expected and launcher[len(expected):] in ([], ["$@"]),
                    "Launcher points outside the declared packaged runtime")
    return installed_size


def load_manifest(source: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationError("Cannot read debs/manifest.json") from exc
    require(isinstance(raw, dict) and raw.get("schemaVersion") == 1 and isinstance(raw.get("applications"), list), "Invalid manifest schema")
    require(0 < len(raw["applications"]) <= 1000, "Manifest must contain 1–1000 applications")
    return raw["applications"]


def inspect_packages(source: Path = DEFAULT_SOURCE) -> list[dict[str, Any]]:
    entries = []
    ids: set[str] = set()
    packages: set[str] = set()
    filenames: set[str] = set()
    for metadata in load_manifest(source):
        require(isinstance(metadata, dict), "Application metadata must be an object")
        filename, identity = metadata.get("filename"), metadata.get("id")
        require(isinstance(filename, str) and bool(FILENAME_RE.fullmatch(filename)), "Invalid flat deb filename")
        require(isinstance(identity, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", identity)) and identity not in ids, "Invalid or duplicate application id")
        require(filename not in filenames, "Duplicate artifact filename")
        path = source / filename
        require(path.is_file() and not path.is_symlink(), f"Missing regular complete deb: {filename}")
        require(0 < path.stat().st_size <= MAX_DEB_BYTES, "deb size outside 1 byte–512 MB policy")
        digest = sha256_file(path)
        require(isinstance(metadata.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is not None and metadata["sha256"] == digest,
                f"{filename}: SHA-256 does not match the reviewed manifest")
        fields = package_fields(path)
        require(metadata.get("package") == fields["Package"] and fields["Package"] not in packages,
                "Manifest package conflicts with deb control or another entry")
        for name in ("name", "summary", "description"):
            value = metadata.get(name)
            require(isinstance(value, dict) and bool(value) and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in value.items()), f"{name} requires nonempty localized text")
        require(isinstance(metadata.get("compatibility"), dict), "compatibility must be an object")
        compatibility = dict(metadata["compatibility"])
        require(compatibility.get("arch") == ["arm64"], "Manifest must target the official ARM64 image")
        systems = [value.strip() for value in fields["X-Typix-Compatible-OS"].split(",")]
        require("os" not in compatibility or compatibility["os"] == systems, "Manifest OS compatibility conflicts with deb control")
        compatibility["os"] = systems
        for name in ("minMemoryMB", "minFreeDiskMB"):
            require(type(compatibility.get(name)) is int and compatibility[name] >= 0, f"Missing/invalid {name}")
        for name in ("display", "requiredFeatures"):
            require(isinstance(compatibility.get(name), list) and all(isinstance(value, str) for value in compatibility[name]), f"Invalid {name}")
        require(set(compatibility["display"]).issubset({"wayland", "x11"}), "Unsupported display declaration")
        require(set(compatibility["requiredFeatures"]).issubset({"wayland", "x11", "touch", "keyboard", "audio", "network", "opengl-es-3", "vulkan", "nvme", "usb-serial"}), "Unknown required feature")
        require(isinstance(metadata.get("categories", []), list) and all(isinstance(value, str) for value in metadata.get("categories", [])), "Invalid categories")
        installed_size = verify_complete_payload(path, metadata)
        compatibility["minFreeDiskMB"] = max(compatibility["minFreeDiskMB"], (installed_size + 1024 * 1024 - 1) // (1024 * 1024) + 64)
        entries.append({"id": identity, "package": fields["Package"], "currentVersion": fields["Version"],
                        "name": metadata["name"], "summary": metadata["summary"], "description": metadata["description"],
                        "categories": metadata.get("categories", []), "desktopFile": metadata["desktopFile"],
                        "versions": [{"version": fields["Version"], "artifact": {
                            "filename": filename, "arch": fields["Architecture"], "sha256": digest,
                            "sizeBytes": path.stat().st_size, "installedSizeBytes": installed_size,
                            "depends": fields["Depends"], "payload": "complete-deb"},
                            "compatibility": compatibility}]})
        ids.add(identity)
        packages.add(fields["Package"])
        filenames.add(filename)
    extra = {path.name for path in source.glob("*.deb")} - filenames
    require(not extra, "Unreviewed deb files are not silently published: " + ", ".join(sorted(extra)))
    return entries


def load_key(path: Path, *, create: bool = False) -> Ed25519PrivateKey:
    path = path.expanduser()
    require(not path.absolute().is_relative_to(ROOT) and not path.resolve().is_relative_to(ROOT), "Signing private key must stay outside the repository")
    if not path.exists():
        require(create, "Signing key does not exist; generate it explicitly outside the repository")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = Ed25519PrivateKey.generate()
        data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        with path.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
        return key
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, "Private key must be a file readable only by its owner (chmod 600)")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise PublicationError("Invalid private signing key") from exc
    require(isinstance(key, Ed25519PrivateKey), "Signing key must be Ed25519")
    return key


def catalog_bytes(entries: list[dict], *, channel: str, repository: str | None = None,
                  days: int = 90, now: datetime | None = None) -> bytes:
    require(channel in {"development-offline", "github-release", "github-repository"}, "Unsupported catalog channel")
    require(type(days) is int and 1 <= days <= 365, "Expiry must be 1–365 days")
    if channel != "development-offline":
        require(isinstance(repository, str) and bool(REPOSITORY_RE.fullmatch(repository)) and not repository.endswith(".git"), "Supply the actual GitHub repository as owner/repo")
    now = now or datetime.now(timezone.utc)
    require(now.tzinfo is not None, "Publication timestamp must include a timezone")
    catalog = {"schemaVersion": 1, "channel": channel, "revision": now.strftime("%Y%m%dT%H%M%SZ"),
               "generatedAt": now.isoformat(), "expiresAt": (now + timedelta(days=days)).isoformat(), "applications": entries}
    if repository is not None:
        catalog["repository"] = repository
    return (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_publication(source: Path, output: Path, key: Ed25519PrivateKey, *, channel: str,
                      repository: str | None = None, days: int = 90) -> dict[str, str]:
    entries = inspect_packages(source)
    if channel == "github-repository":
        require(all(entry["versions"][0]["artifact"]["sizeBytes"] <= 100 * 1024 * 1024 for entry in entries),
                "Raw Git publication policy limits each complete deb to 100 MB; use Release assets for larger apps")
    raw = catalog_bytes(entries, channel=channel, repository=repository, days=days)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".typix-publication-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for entry in entries:
            artifact = entry["versions"][0]["artifact"]
            filename = artifact["filename"]
            shutil.copyfile(source / filename, stage / filename)
            require(sha256_file(stage / filename) == artifact["sha256"], "Source package changed during publication")
        (stage / "catalog.json").write_bytes(raw)
        (stage / "catalog.json.sig").write_bytes(key.sign(raw))
        (stage / "public-key.pem").write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        shutil.copyfile(source / "manifest.json", stage / "manifest.json")
        hashes = {path.name: sha256_file(path) for path in sorted(stage.iterdir())}
        (stage / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()), encoding="utf-8")
        output.mkdir(parents=True, exist_ok=True)
        stale = {path.name for path in output.glob("*.deb")} - {entry["versions"][0]["artifact"]["filename"] for entry in entries}
        require(not stale, "Output contains stale/unreviewed debs; use a fresh output directory")
        for path in stage.iterdir():
            os.replace(path, output / path.name)
    return hashes
