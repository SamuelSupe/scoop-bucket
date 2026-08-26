#!/usr/bin/env python3
"""Generate the git-rg Scoop manifest from a verified GitHub Release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import NoReturn
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY = "SamuelSupe/git-rg"
API_BASE = f"https://api.github.com/repos/{REPOSITORY}"
RELEASE_BASE = f"https://github.com/{REPOSITORY}/releases/download"
MINIMUM_VERSION = (0, 2, 0)
ARCHIVES = {
    "windows_amd64": "git-rg_{tag}_windows_amd64.zip",
    "windows_arm64": "git-rg_{tag}_windows_arm64.zip",
}
ALL_ARCHIVES = {
    "linux_amd64": "git-rg_{tag}_linux_amd64.tar.gz",
    "linux_arm64": "git-rg_{tag}_linux_arm64.tar.gz",
    "darwin_amd64": "git-rg_{tag}_darwin_amd64.tar.gz",
    "darwin_arm64": "git-rg_{tag}_darwin_arm64.tar.gz",
    **ARCHIVES,
}
SCRIPT_ASSETS = ("install.sh", "install.ps1")
CHECKSUMS_ASSET = "checksums.txt"
VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a GitHub token to a different host after a redirect."""

    def redirect_request(self, request, file, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        redirected = super().redirect_request(request, file, code, message, headers, new_url)
        if redirected is None:
            return None
        old_host = urllib.parse.urlsplit(request.full_url).netloc.lower()
        new_host = urllib.parse.urlsplit(new_url).netloc.lower()
        if old_host != new_host:
            for header in list(redirected.headers):
                if header.lower() in {"authorization", "cookie", "private-token"}:
                    del redirected.headers[header]
        return redirected


OPENER = urllib.request.build_opener(SafeRedirectHandler())


class GenerationError(RuntimeError):
    """An expected release validation failure."""


def fail(message: str) -> NoReturn:
    raise GenerationError(message)


def fetch(url: str) -> bytes:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "git-rg-scoop-bucket"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with OPENER.open(request, timeout=60) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    fail(f"release asset is larger than the 1 GiB validation limit: {url}")
                chunks.append(chunk)
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"download failed for {url}: {exc}")


def fetch_json(url: str) -> object:
    try:
        return json.loads(fetch(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"GitHub returned invalid JSON: {exc}")


def releases() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for page in range(1, 11):
        payload = fetch_json(f"{API_BASE}/releases?per_page=100&page={page}")
        if not isinstance(payload, list):
            fail("GitHub releases API returned an unexpected response")
        page_items = [item for item in payload if isinstance(item, dict)]
        result.extend(page_items)
        if len(page_items) < 100:
            break
    else:
        fail("GitHub releases API exceeded the pagination safety limit")
    return result


def parse_tag(tag: object) -> tuple[int, int, int]:
    if not isinstance(tag, str):
        fail("stable release has no tag_name")
    match = VERSION_RE.fullmatch(tag)
    if match is None:
        fail(f"stable release tag {tag!r} is not strict vX.Y.Z")
    return tuple(int(part) for part in match.groups())


def select_release() -> tuple[str, tuple[int, int, int], dict[str, object]]:
    stable = [
        item
        for item in releases()
        if not bool(item.get("draft")) and not bool(item.get("prerelease"))
    ]
    if not stable:
        fail("no non-draft, non-prerelease GitHub Release exists")
    candidates: list[tuple[tuple[int, int, int], str, dict[str, object]]] = []
    for item in stable:
        tag = item.get("tag_name")
        version = parse_tag(tag)
        candidates.append((version, tag, item))  # type: ignore[arg-type]
    version, tag, release = max(candidates, key=lambda item: item[0])
    if version < MINIMUM_VERSION:
        fail("latest stable release is older than the required v0.2.0")
    return tag, version, release


def release_assets(release: dict[str, object]) -> dict[str, str]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        fail("selected release has no asset list")
    assets: dict[str, str] = {}
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        fail("selected release has no tag_name")
    expected_prefix = f"{RELEASE_BASE}/{tag}/"
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        name = raw_asset.get("name")
        url = raw_asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            if name in assets:
                fail(f"selected release contains duplicate asset {name}")
            if url != f"{expected_prefix}{name}":
                fail(f"selected release asset URL is not the canonical GitHub URL: {name}")
            assets[name] = url

    required = [template.format(tag=tag) for template in ALL_ARCHIVES.values()]
    required.extend(SCRIPT_ASSETS)
    required.append(CHECKSUMS_ASSET)
    missing = [name for name in required if name not in assets]
    if missing:
        fail("selected release is missing required assets: " + ", ".join(missing))
    return assets


def checksum_map(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"checksums.txt is not UTF-8: {exc}")
    parsed: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2 or not HEX_RE.fullmatch(fields[0]):
            fail(f"checksums.txt line {line_number} is not a SHA-256 filename entry")
        name, digest = fields[1], fields[0].lower()
        if name in parsed:
            fail(f"checksums.txt contains duplicate entry for {name}")
        parsed[name] = digest
    return parsed


def verify_assets(tag: str, assets: dict[str, str]) -> dict[str, str]:
    checksums = checksum_map(fetch(assets[CHECKSUMS_ASSET]))
    names = [template.format(tag=tag) for template in ALL_ARCHIVES.values()]
    names.extend(SCRIPT_ASSETS)
    missing = [name for name in names if name not in checksums]
    if missing:
        fail("checksums.txt is missing required entries: " + ", ".join(missing))

    for name in names:
        expected = checksums[name]
        actual = hashlib.sha256(fetch(assets[name])).hexdigest()
        if actual != expected:
            fail(f"checksum mismatch for {name}")
    return {name: checksums[name] for name in names}


def manifest(tag: str, version: tuple[int, int, int], checksums: dict[str, str]) -> dict[str, object]:
    version_text = ".".join(str(part) for part in version)

    def values(key: str) -> tuple[str, str]:
        name = ARCHIVES[key].format(tag=tag)
        return f"{RELEASE_BASE}/{tag}/{name}", checksums[name]

    windows_amd64 = values("windows_amd64")
    windows_arm64 = values("windows_arm64")
    return {
        "version": version_text,
        "description": "Remote ripgrep for GitHub and GitLab without cloning repository history",
        "homepage": f"https://github.com/{REPOSITORY}",
        "license": "MIT",
        "architecture": {
            "64bit": {
                "url": windows_amd64[0],
                "hash": windows_amd64[1],
                "extract_dir": f"git-rg_v{version_text}_windows_amd64",
            },
            "arm64": {
                "url": windows_arm64[0],
                "hash": windows_arm64[1],
                "extract_dir": f"git-rg_v{version_text}_windows_arm64",
            },
        },
        "bin": "git-rg.exe",
        "checkver": {"github": f"https://github.com/{REPOSITORY}"},
        "autoupdate": {
            "architecture": {
                "64bit": {
                    "url": f"{RELEASE_BASE}/v$version/git-rg_v$version_windows_amd64.zip",
                    "extract_dir": "git-rg_v$version_windows_amd64",
                    "hash": {
                        "url": f"{RELEASE_BASE}/v$version/checksums.txt",
                        "regex": r"$sha256\s+git-rg_v$version_windows_amd64\.zip",
                    },
                },
                "arm64": {
                    "url": f"{RELEASE_BASE}/v$version/git-rg_v$version_windows_arm64.zip",
                    "extract_dir": "git-rg_v$version_windows_arm64",
                    "hash": {
                        "url": f"{RELEASE_BASE}/v$version/checksums.txt",
                        "regex": r"$sha256\s+git-rg_v$version_windows_arm64\.zip",
                    },
                },
            }
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("bucket/git-rg.json"))
    args = parser.parse_args()
    try:
        tag, version, release = select_release()
        assets = release_assets(release)
        checksums = verify_assets(tag, assets)
        content = json.dumps(manifest(tag, version, checksums), indent=2, ensure_ascii=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(args.output)
    except GenerationError as exc:
        print(f"generate-manifest: {exc}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
