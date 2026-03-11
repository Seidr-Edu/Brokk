from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from brokk.models import Manifest

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
GITHUB_PATH_RE = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?/?$")
ALLOWED_KEYS = {"version", "run_id", "repo_url", "commit_sha"}


class ManifestError(ValueError):
    """Manifest content is invalid."""


class RepoUrlError(ManifestError):
    """Repository URL is invalid."""


class CommitShaError(ManifestError):
    """Commit SHA is invalid."""


def load_manifest(path: Path, *, default_run_id: str, allow_file_urls: bool) -> Manifest:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"manifest not found or unreadable: {path}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest must be a YAML mapping")

    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise ManifestError(f"unknown manifest keys: {', '.join(unknown)}")

    version = data.get("version")
    if version != 1:
        raise ManifestError(f"unsupported manifest version: {version!r}")

    run_id = data.get("run_id") or default_run_id
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ManifestError("run_id must contain only letters, digits, '.', '_' or '-'")

    repo_url = data.get("repo_url")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise RepoUrlError("repo_url is required")
    host, transport = validate_repo_url(repo_url.strip(), allow_file_urls=allow_file_urls)

    commit_sha = data.get("commit_sha")
    if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
        raise CommitShaError("commit_sha must be a full 40-character SHA")

    return Manifest(
        version=1,
        run_id=run_id,
        repo_url=repo_url.strip(),
        commit_sha=commit_sha.lower(),
        host=host,
        transport=transport,
        allow_file_urls=allow_file_urls,
    )


def validate_repo_url(repo_url: str, *, allow_file_urls: bool) -> tuple[str, str]:
    parsed = urlparse(repo_url)

    if allow_file_urls and parsed.scheme == "file":
        if not parsed.path:
            raise RepoUrlError("file:// repo_url must point to a repository path")
        return "local", "file"

    if parsed.scheme != "https":
        raise RepoUrlError("repo_url must use https")
    if parsed.hostname != "github.com":
        raise RepoUrlError("repo_url must point to github.com")
    if parsed.username or parsed.password:
        raise RepoUrlError("repo_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise RepoUrlError("repo_url must not include query or fragment components")

    match = GITHUB_PATH_RE.fullmatch(parsed.path)
    if match is None:
        raise RepoUrlError("repo_url must match https://github.com/<owner>/<repo>[.git]")

    owner, repo = match.groups()
    if not owner or not repo or repo in {".git", ""}:
        raise RepoUrlError("repo_url must identify a GitHub repository")

    return "github.com", "https"

