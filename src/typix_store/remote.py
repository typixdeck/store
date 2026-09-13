"""Pinned public GitHub sources, bounded downloads, and signed offline fallback."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .catalog import CatalogError, load_catalog_file, verify_hash
from .client import require_system_owned

CONFIG_PATH = Path("/etc/typix-store/github.json")
MAX_CATALOG = 4 * 1024 * 1024
MAX_PACKAGE = 1024 * 1024 * 1024
GITHUB_HOSTS = {"github.com", "raw.githubusercontent.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


@dataclass(frozen=True)
class GitHubConfig:
    repository: str
    public_key: Path
    mode: str = "release"
    release: str = "latest"
    ref: str = "main"
    directory: str = "apps/store/debs"

    @property
    def channel(self):
        return "github-repository" if self.mode == "raw" else "github-release"

    def asset_url(self, filename):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_~:-]*", filename):
            raise CatalogError("GitHub 文件名非法")
        filename = urllib.parse.quote(filename, safe="")
        if self.mode == "raw":
            return f"https://raw.githubusercontent.com/{self.repository}/{urllib.parse.quote(self.ref, safe='')}/{self.directory}/{filename}"
        route = "latest/download" if self.release == "latest" else "download/" + urllib.parse.quote(self.release, safe="")
        return f"https://github.com/{self.repository}/releases/{route}/{filename}"

    @property
    def cache_key(self):
        return hashlib.sha256(f"{self.repository}:{self.mode}:{self.release}:{self.ref}:{self.directory}:{self.public_key}".encode()).hexdigest()[:24]


def load_config(path=CONFIG_PATH):
    require_system_owned(path)
    if path.stat().st_size > 8192:
        raise CatalogError("GitHub 配置过大")
    try:
        raw = json.loads(path.read_bytes())
        repository = raw["repository"]
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
            raise ValueError("repository")
        key = Path(raw["publicKey"])
        if not key.is_absolute():
            raise ValueError("publicKey")
        require_system_owned(key)
        mode = raw.get("mode", "release")
        if mode not in {"release", "raw"}:
            raise ValueError("mode")
        release, ref = raw.get("release", "latest"), raw.get("ref", "main")
        for value in (release, ref):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", value) or ".." in value:
                raise ValueError("release/ref")
        directory = raw.get("directory", "apps/store/debs")
        if not isinstance(directory, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*", directory) or ".." in directory:
            raise ValueError("directory")
        return GitHubConfig(repository, key, mode, release, ref, directory)
    except (KeyError, ValueError, TypeError) as exc:
        raise CatalogError("GitHub 系统配置无效，请管理员检查仓库、版本和公钥") from exc


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in GITHUB_HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise CatalogError("下载跳转超出允许的 GitHub HTTPS 主机")
    return url


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url, headers=None):
    request = urllib.request.Request(checked_url(url), headers={"User-Agent": "TypixStore/0.3", "Accept-Encoding": "identity", **(headers or {})})
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), GitHubRedirect()).open(request, timeout=10)


def check_cancel(cancelled, deadline):
    if cancelled():
        raise CatalogError("下载已取消；已验证前不会开始安装，可重试继续下载")
    if time.monotonic() > deadline:
        raise CatalogError("下载超时，请检查网络后重试")


def fetch_small(url, limit, cancelled=lambda: False, opener=open_url):
    deadline = time.monotonic() + 30
    result = bytearray()
    with opener(url) as response:
        checked_url(response.geturl())
        if response.status != 200:
            raise CatalogError("GitHub 返回了非完整目录响应")
        while True:
            check_cancel(cancelled, deadline)
            block = response.read1(min(65536, limit + 1 - len(result)))
            if not block:
                break
            result.extend(block)
            if len(result) > limit:
                raise CatalogError("GitHub 响应超过大小上限")
    return bytes(result)


class GitHubSource:
    def __init__(self, config, cache=None, opener=open_url):
        self.config = config
        self.cache = (cache or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "typix-store/github") / config.cache_key
        self.opener = opener
        self.catalog = None
        self.signature = None
        self.notice = ""
        self.failure_kind = None

    def verify(self, catalog, signature):
        return load_catalog_file(catalog, signature=signature, public_key=self.config.public_key,
                                 expected_repository=self.config.repository, expected_channel=self.config.channel)

    def load(self, cancelled=lambda: False):
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        pointer = self.cache / "current"
        try:
            raw = fetch_small(self.config.asset_url("catalog.json"), MAX_CATALOG, cancelled, self.opener)
            signature = fetch_small(self.config.asset_url("catalog.json.sig"), 64, cancelled, self.opener)
            if shutil.disk_usage(self.cache).free < len(raw) + 1024 * 1024:
                raise CatalogError("空间不足，无法保存已签名应用目录")
            with tempfile.TemporaryDirectory(prefix="catalog-", dir=self.cache) as temporary:
                catalog = Path(temporary) / "catalog.json"
                sig = Path(temporary) / "catalog.json.sig"
                catalog.write_bytes(raw)
                sig.write_bytes(signature)
                apps = self.verify(catalog, sig)
                digest = hashlib.sha256(raw).hexdigest()
                final = self.cache / digest
                if not final.exists():
                    final.mkdir(mode=0o700)
                os.replace(catalog, final / "catalog.json")
                os.replace(sig, final / "catalog.json.sig")
                temporary_pointer = Path(temporary) / "current"
                temporary_pointer.write_text(digest)
                os.replace(temporary_pointer, pointer)
                self.catalog, self.signature = final / "catalog.json", final / "catalog.json.sig"
                self.notice = "GitHub 目录已验签 · 下载完整 deb 后由系统授权安装"
                # Retain one snapshot; cache failures never invalidate verified data.
                for old in self.cache.iterdir():
                    if old.is_dir() and not old.is_symlink() and re.fullmatch(r"[0-9a-f]{64}", old.name) and old.name != digest:
                        shutil.rmtree(old)
                return apps
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.failure_kind = "unpublished" if isinstance(exc, urllib.error.HTTPError) and exc.code == 404 else "unavailable"
            if cancelled():
                raise CatalogError("目录刷新已取消") from exc
            try:
                digest = pointer.read_text().strip()
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise CatalogError("缓存指针无效")
                catalog = self.cache / digest / "catalog.json"
                signature = self.cache / digest / "catalog.json.sig"
                apps = self.verify(catalog, signature)
                self.catalog, self.signature = catalog, signature
                self.notice = "网络或远程验证失败，正在使用仍有效的已签名缓存；下载可重试"
                return apps
            except (OSError, ValueError):
                raise CatalogError("GitHub 目录不可用且没有有效签名缓存，请检查网络或发布内容后刷新") from exc

    def download(self, app, cancelled=lambda: False, progress=lambda *_: None):
        artifact = app.current_record()["artifact"]
        size, digest = artifact["sizeBytes"], artifact["sha256"].lower()
        if size > MAX_PACKAGE:
            raise CatalogError("软件包超过 1 GiB 下载上限")
        downloads = self.cache / "downloads"
        downloads.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = downloads / (digest + ".deb")
        partial = downloads / (digest + ".part")
        if destination.is_file() and not destination.is_symlink() and destination.stat().st_size == size:
            try:
                verify_hash(destination, digest, cancelled)
                return destination
            except CatalogError:
                if cancelled():
                    raise
                destination.unlink()
        for candidate in (destination, partial):
            if candidate.is_symlink():
                raise CatalogError("下载缓存路径异常，请清理缓存后重试")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            partial.unlink()
            offset = 0
        # One resumable artifact plus one complete artifact fits this fixed budget.
        for old in downloads.iterdir():
            if old not in (partial, destination) and not old.is_dir():
                old.unlink()
        free = shutil.disk_usage(downloads).free
        if free < max(64 * 1024 * 1024, size - offset + 16 * 1024 * 1024):
            raise CatalogError("下载空间不足，请释放空间后重试；已有进度保留")
        if offset == size:
            try:
                verify_hash(partial, digest, cancelled)
            except CatalogError:
                if cancelled():
                    raise
                partial.unlink()
                offset = 0
            else:
                os.replace(partial, destination)
                return destination
        deadline = time.monotonic() + 600
        try:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with self.opener(self.config.asset_url(artifact["filename"]), headers) as response:
                checked_url(response.geturl())
                if response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or tuple(map(int, match.groups())) != (offset, size - 1, size):
                        raise CatalogError("续传范围与签名大小不一致")
                elif response.status == 200:
                    offset = 0  # Server ignored Range; restart, never append.
                else:
                    raise CatalogError("GitHub 下载响应异常")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) != size - offset):
                    raise CatalogError("下载长度与签名大小不一致")
                with partial.open("ab" if offset else "wb") as output:
                    while True:
                        check_cancel(cancelled, deadline)
                        block = response.read1(min(262144, size - offset + 1))
                        if not block:
                            break
                        offset += len(block)
                        if offset > size:
                            raise CatalogError("下载超出签名声明大小")
                        output.write(block)
                        progress(offset, size)
            if offset != size:
                raise CatalogError("下载中断，已保存进度；重试可继续下载")
            try:
                verify_hash(partial, digest, cancelled)
            except CatalogError:
                if not cancelled():
                    partial.unlink(missing_ok=True)
                raise
            os.replace(partial, destination)
            return destination
        except (OSError, urllib.error.URLError) as exc:
            raise CatalogError("网络连接失败，已保留下载进度；请重试") from exc
