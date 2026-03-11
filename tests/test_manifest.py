from __future__ import annotations

from pathlib import Path

import pytest

from brokk.manifest import CommitShaError, ManifestError, RepoUrlError, load_manifest


def test_load_manifest_accepts_valid_github_url(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "run_id: test-run",
                "repo_url: https://github.com/octocat/Hello-World.git",
                "commit_sha: 0123456789abcdef0123456789abcdef01234567",
            ]
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path, default_run_id="fallback", allow_file_urls=False)

    assert manifest.run_id == "test-run"
    assert manifest.host == "github.com"
    assert manifest.transport == "https"


def test_load_manifest_rejects_unknown_key(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "repo_url: https://github.com/octocat/Hello-World.git",
                "commit_sha: 0123456789abcdef0123456789abcdef01234567",
                "unexpected: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError):
        load_manifest(manifest_path, default_run_id="fallback", allow_file_urls=False)


def test_load_manifest_rejects_non_github_url(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "repo_url: https://gitlab.com/octocat/Hello-World.git",
                "commit_sha: 0123456789abcdef0123456789abcdef01234567",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RepoUrlError):
        load_manifest(manifest_path, default_run_id="fallback", allow_file_urls=False)


def test_load_manifest_rejects_short_sha(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "repo_url: https://github.com/octocat/Hello-World.git",
                "commit_sha: 1234567",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CommitShaError):
        load_manifest(manifest_path, default_run_id="fallback", allow_file_urls=False)


def test_load_manifest_allows_file_urls_when_enabled(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                f"repo_url: {tmp_path.as_uri()}",
                "commit_sha: 0123456789abcdef0123456789abcdef01234567",
            ]
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path, default_run_id="fallback", allow_file_urls=True)

    assert manifest.host == "local"
    assert manifest.transport == "file"

