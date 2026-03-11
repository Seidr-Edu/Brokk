from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import git_lfs_available, run_service, write_manifest


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_service_clones_exact_historical_commit(
    history_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                "run_id: test-run",
                f"repo_url: {history_remote['remote'].as_uri()}",
                f"commit_sha: {history_remote['first_commit']}",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    source_manifest = load_json(run_dir / "inputs" / "source-manifest.json")
    exported_readme = (run_dir / "artifacts" / "original-repo" / "README.md").read_text(
        encoding="utf-8"
    )

    assert report["status"] == "passed"
    assert report["resolved_commit"] == history_remote["first_commit"]
    assert source_manifest["resolved_commit"] == history_remote["first_commit"]
    assert exported_readme == "first\n"
    assert not (run_dir / "artifacts" / "original-repo" / ".git").exists()


def test_service_emits_error_report_for_malformed_yaml(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    manifest_path.write_text("version: 1\nrepo_url: [bad\n", encoding="utf-8")

    result = run_service(run_dir, manifest_path, allow_file_urls=False)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["status"] == "error"
    assert report["reason"] == "invalid-manifest"


def test_service_emits_error_report_for_unknown_key(
    history_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                f"repo_url: {history_remote['remote'].as_uri()}",
                f"commit_sha: {history_remote['first_commit']}",
                "unexpected: true",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["reason"] == "invalid-manifest"


def test_service_emits_error_report_for_invalid_sha(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                "repo_url: https://github.com/octocat/Hello-World.git",
                "commit_sha: 1234567",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path, allow_file_urls=False)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["reason"] == "invalid-commit-sha"


def test_service_emits_failure_for_missing_commit(
    history_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    missing_commit = "f" * 40
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                f"repo_url: {history_remote['remote'].as_uri()}",
                f"commit_sha: {missing_commit}",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["status"] == "failed"
    assert report["reason"] == "commit-not-found"


def test_service_materializes_recursive_submodules(
    submodule_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                f"repo_url: {submodule_remote['remote'].as_uri()}",
                f"commit_sha: {submodule_remote['commit']}",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    submodule_file = (
        run_dir / "artifacts" / "original-repo" / "deps" / "submodule" / "submodule.txt"
    )

    assert report["status"] == "passed"
    assert report["submodules_materialized"] is True
    assert submodule_file.read_text(encoding="utf-8") == "submodule\n"


@pytest.mark.lfs
def test_service_materializes_git_lfs_content(
    lfs_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    if not git_lfs_available():
        pytest.skip("git-lfs not available")

    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True)
    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                f"repo_url: {lfs_remote['remote'].as_uri()}",
                f"commit_sha: {lfs_remote['commit']}",
            ]
        ),
    )

    result = run_service(run_dir, manifest_path)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    asset_contents = (run_dir / "artifacts" / "original-repo" / "asset.bin").read_bytes()

    assert report["status"] == "passed"
    assert report["lfs_materialized"] is True
    assert not asset_contents.startswith(b"version https://git-lfs.github.com/spec/v1\n")


def test_service_returns_one_when_run_dir_not_writable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    os.chmod(run_dir, 0o500)
    manifest_path = run_dir / "config" / "manifest.yaml"

    try:
        result = run_service(run_dir, manifest_path, allow_file_urls=False)
    finally:
        os.chmod(run_dir, 0o700)

    assert result.returncode == 1
    assert not (run_dir / "outputs" / "run_report.json").exists()


def test_service_emits_report_when_runtime_artifacts_dir_is_not_writable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    outputs_dir = run_dir / "outputs"
    config_dir.mkdir(parents=True)
    outputs_dir.mkdir()

    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                "run_id: partial-run",
                "repo_url: https://github.com/octocat/Hello-World.git",
                "commit_sha: 0123456789abcdef0123456789abcdef01234567",
            ]
        ),
    )

    os.chmod(run_dir, 0o555)
    try:
        result = run_service(run_dir, manifest_path, allow_file_urls=False)
    finally:
        os.chmod(run_dir, 0o755)

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["status"] == "error"
    assert report["reason"] == "run-dir-not-writable"
