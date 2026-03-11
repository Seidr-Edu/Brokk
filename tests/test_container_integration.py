from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ROOT_DIR, docker_available, git_lfs_available, write_manifest

pytestmark = pytest.mark.container


IMAGE_TAG = "brokk:local"


def docker_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


@pytest.fixture(scope="session")
def built_image() -> str:
    if not docker_available():
        pytest.skip("docker not available")
    if docker_run(["docker", "version"]).returncode != 0:
        pytest.skip("docker daemon unavailable")

    build = docker_run(["docker", "build", "-t", IMAGE_TAG, str(ROOT_DIR)])
    if build.returncode != 0:
        pytest.fail(build.stderr or build.stdout)
    return IMAGE_TAG


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_container_default_user_is_non_root(built_image: str) -> None:
    result = docker_run(["docker", "run", "--rm", "--entrypoint", "id", built_image, "-u"])
    assert result.returncode == 0
    assert result.stdout.strip() != "0"


def test_container_emits_error_report_for_missing_manifest(
    built_image: str,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run_dir.chmod(0o777)

    result = docker_run(["docker", "run", "--rm", "-v", f"{run_dir}:/run", built_image])

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["reason"] == "invalid-manifest"


def test_container_run_emits_outputs(
    built_image: str,
    history_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = tmp_path / "config"
    fixture_root = tmp_path / "fixtures"
    run_dir.mkdir()
    config_dir.mkdir()
    fixture_root.mkdir()
    run_dir.chmod(0o777)
    remote_path = fixture_root / history_remote["remote"].name
    shutil.copytree(history_remote["remote"], remote_path)
    for path in (config_dir, fixture_root, remote_path):
        path.chmod(0o755)

    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                "run_id: container-run",
                f"repo_url: file:///fixtures/{remote_path.name}",
                f"commit_sha: {history_remote['first_commit']}",
            ]
        ),
    )

    result = docker_run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "BROKK_MANIFEST=/run/config/manifest.yaml",
            "-e",
            "BROKK_ALLOW_FILE_URLS=1",
            "-v",
            f"{config_dir}:/run/config:ro",
            "-v",
            f"{run_dir}:/run",
            "-v",
            f"{fixture_root}:/fixtures:ro",
            built_image,
        ]
    )

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    assert report["status"] == "passed"
    assert report["resolved_commit"] == history_remote["first_commit"]
    exported_readme = (run_dir / "artifacts" / "original-repo" / "README.md").read_text(
        encoding="utf-8"
    )

    assert exported_readme == "first\n"


@pytest.mark.lfs
def test_container_materializes_lfs_content(
    built_image: str,
    lfs_remote: dict[str, object],
    tmp_path: Path,
) -> None:
    if not git_lfs_available():
        pytest.skip("git-lfs not available")

    run_dir = tmp_path / "run"
    config_dir = tmp_path / "config"
    fixture_root = tmp_path / "fixtures"
    run_dir.mkdir()
    config_dir.mkdir()
    fixture_root.mkdir()
    run_dir.chmod(0o777)
    remote_path = fixture_root / lfs_remote["remote"].name
    shutil.copytree(lfs_remote["remote"], remote_path)
    for path in (config_dir, fixture_root, remote_path):
        path.chmod(0o755)

    manifest_path = config_dir / "manifest.yaml"
    write_manifest(
        manifest_path,
        "\n".join(
            [
                "version: 1",
                f"repo_url: file:///fixtures/{remote_path.name}",
                f"commit_sha: {lfs_remote['commit']}",
            ]
        ),
    )

    result = docker_run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "BROKK_MANIFEST=/run/config/manifest.yaml",
            "-e",
            "BROKK_ALLOW_FILE_URLS=1",
            "-v",
            f"{config_dir}:/run/config:ro",
            "-v",
            f"{run_dir}:/run",
            "-v",
            f"{fixture_root}:/fixtures:ro",
            built_image,
        ]
    )

    assert result.returncode == 0
    report = load_json(run_dir / "outputs" / "run_report.json")
    asset_contents = (run_dir / "artifacts" / "original-repo" / "asset.bin").read_bytes()
    assert report["status"] == "passed"
    assert report["lfs_materialized"] is True
    assert not asset_contents.startswith(b"version https://git-lfs.github.com/spec/v1\n")
