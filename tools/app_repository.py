"""Discover declarative app.json submissions without executing repository code."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from store_publication import PublicationError, inspect_packages, package_fields, require


def local_file(root: Path, value: str, prefix: str) -> Path:
    require(isinstance(value, str), "Expected repository-relative path")
    relative = PurePosixPath(value)
    require(bool(relative.parts) and not relative.is_absolute() and '..' not in relative.parts and relative.parts[0] == prefix,
            f"Path must stay inside {prefix}/")
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), "Repository symlinks are not accepted")
    require(path.is_file(), f"Missing repository file: {value}")
    return path


def descriptor(root: Path) -> tuple[dict, Path]:
    path = root / 'app.json'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024,
            'app.json must be a regular file no larger than 64 KiB')
    try:
        raw = json.loads(path.read_text())
    except (ValueError, UnicodeError) as exc:
        raise PublicationError('Invalid app.json') from exc
    require(isinstance(raw, dict) and raw.get('schemaVersion') == 1, 'Unsupported app.json schema')
    require(raw.get('distribution') == 'complete-deb', 'Only complete deb submissions can be imported')
    repo = raw.get('repository')
    require(isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo), 'Invalid repository owner/name')
    metadata, release = raw.get('application'), raw.get('release')
    require(isinstance(metadata, dict) and isinstance(release, dict), 'Missing application/release')
    package = local_file(root, release.get('file'), 'dist')
    require(package.suffix == '.deb', 'Release must reference dist/*.deb')
    require(package_fields(package)['Version'] == release.get('version'), 'Release version conflicts with deb control')
    metadata = dict(metadata, filename=package.name, sha256=release.get('sha256'))
    screenshots = raw.get('screenshots', [])
    require(isinstance(screenshots, list) and 1 <= len(screenshots) <= 8, 'Provide 1–8 real screenshots')
    for shot in screenshots:
        require(isinstance(shot, dict) and isinstance(shot.get('caption'), str) and shot['caption'].strip(), 'Screenshot caption required')
        pic = local_file(root, shot.get('path'), 'docs')
        require(PurePosixPath(shot['path']).parts[:2] == ('docs', 'screenshots'), 'Use docs/screenshots/')
        require(pic.suffix.lower() in {'.png', '.jpg', '.jpeg'} and 0 < pic.stat().st_size <= 8 * 1024**2, 'Invalid screenshot format/size')
        with pic.open('rb') as stream:
            header = stream.read(8)
        require(header == b'\x89PNG\r\n\x1a\n' or header[:3] == b'\xff\xd8\xff', 'Screenshot file signature mismatch')
    # URLs are derived from the declared GitHub repository; this tool never
    # fetches arbitrary URLs or executes build hooks from a submitted repo.
    metadata['sourceRepository'] = repo
    metadata['screenshots'] = screenshots
    return metadata, package


def import_apps(repositories: list[Path], output: Path) -> list[dict]:
    require(repositories, 'No app.json repositories found')
    require(not output.exists() and not output.is_symlink(), 'Output must be new; existing signed sources are never overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.typix-import-', dir=output.parent))
    try:
        applications, filenames, ids, packages = [], set(), set(), set()
        for root in repositories:
            require(not root.is_symlink(), 'Repository directory must not be a symlink')
            metadata, package = descriptor(root)
            require(package.name not in filenames and metadata.get('id') not in ids and metadata.get('package') not in packages,
                    'Duplicate filename, application id or package across repositories')
            filenames.add(package.name); ids.add(metadata.get('id')); packages.add(metadata.get('package'))
            shutil.copyfile(package, temporary / package.name)
            applications.append(metadata)
        (temporary / 'manifest.json').write_text(json.dumps({'schemaVersion': 1, 'applications': applications}, ensure_ascii=False, indent=2) + '\n')
        inspected = inspect_packages(temporary)
        temporary.rename(output)
        return inspected
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
