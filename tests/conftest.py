from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def git_available() -> bool:
    return shutil.which("git") is not None


def git_lfs_available() -> bool:
    if shutil.which("git") is None:
        return False
    result = subprocess.run(
        ["git", "lfs", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def docker_available() -> bool:
    return shutil.which("docker") is not None


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        check=check,
    )


def init_repo(repo_path: Path) -> None:
    run(["git", "init", str(repo_path)])
    run(["git", "-C", str(repo_path), "checkout", "-b", "main"])
    run(["git", "-C", str(repo_path), "config", "user.email", "tests@example.com"])
    run(["git", "-C", str(repo_path), "config", "user.name", "Brokk Tests"])


def commit_all(repo_path: Path, message: str) -> str:
    run(["git", "-C", str(repo_path), "add", "."])
    run(["git", "-C", str(repo_path), "commit", "-m", message])
    return run(["git", "-C", str(repo_path), "rev-parse", "HEAD"]).stdout.strip()


def make_bare_remote(source_repo: Path, remote_path: Path) -> Path:
    run(["git", "clone", "--bare", str(source_repo), str(remote_path)])
    run(["git", "-C", str(source_repo), "remote", "add", "origin", remote_path.as_uri()])
    run(["git", "-C", str(source_repo), "push", "-u", "origin", "main"])
    if repo_uses_lfs(source_repo):
        run(["git", "-C", str(source_repo), "lfs", "push", "--all", "origin"])
    return remote_path


def write_manifest(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def service_env(
    run_dir: Path,
    manifest_path: Path,
    *,
    allow_file_urls: bool = True,
) -> dict[str, str]:
    env = {
        "PYTHONPATH": str(SRC_DIR),
        "BROKK_RUN_DIR": str(run_dir),
        "BROKK_MANIFEST": str(manifest_path),
    }
    if allow_file_urls:
        env["BROKK_ALLOW_FILE_URLS"] = "1"
    return env


def run_service(
    run_dir: Path,
    manifest_path: Path,
    *,
    allow_file_urls: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = service_env(run_dir, manifest_path, allow_file_urls=allow_file_urls)
    return run([sys.executable, "-m", "brokk.service"], env=env, check=False)


def repo_uses_lfs(repo_path: Path) -> bool:
    attributes_path = repo_path / ".gitattributes"
    if not attributes_path.exists():
        return False
    return "filter=lfs" in attributes_path.read_text(encoding="utf-8", errors="ignore")


@pytest.fixture
def history_remote(tmp_path: Path) -> dict[str, object]:
    work = tmp_path / "history-work"
    work.mkdir()
    init_repo(work)

    (work / "README.md").write_text("first\n", encoding="utf-8")
    first_commit = commit_all(work, "first")

    (work / "README.md").write_text("second\n", encoding="utf-8")
    head_commit = commit_all(work, "second")

    remote = make_bare_remote(work, tmp_path / "history-remote.git")
    return {
        "work": work,
        "remote": remote,
        "first_commit": first_commit,
        "head_commit": head_commit,
    }


@pytest.fixture
def submodule_remote(tmp_path: Path) -> dict[str, object]:
    submodule_work = tmp_path / "submodule-work"
    submodule_work.mkdir()
    init_repo(submodule_work)
    (submodule_work / "submodule.txt").write_text("submodule\n", encoding="utf-8")
    commit_all(submodule_work, "submodule initial")
    submodule_remote = make_bare_remote(submodule_work, tmp_path / "submodule-remote.git")

    main_work = tmp_path / "main-work"
    main_work.mkdir()
    init_repo(main_work)
    (main_work / "README.md").write_text("main\n", encoding="utf-8")
    commit_all(main_work, "main initial")

    run(
        [
            "git",
            "-C",
            str(main_work),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            submodule_remote.as_uri(),
            "deps/submodule",
        ]
    )
    commit_all(main_work, "add submodule")

    remote = make_bare_remote(main_work, tmp_path / "main-remote.git")
    commit = run(["git", "-C", str(main_work), "rev-parse", "HEAD"]).stdout.strip()
    return {
        "remote": remote,
        "commit": commit,
        "submodule_remote": submodule_remote,
    }


@pytest.fixture
def lfs_remote(tmp_path: Path) -> dict[str, object]:
    if not git_lfs_available():
        pytest.skip("git-lfs not available")

    work = tmp_path / "lfs-work"
    work.mkdir()
    init_repo(work)
    run(["git", "-C", str(work), "lfs", "install", "--local"])
    run(["git", "-C", str(work), "lfs", "track", "*.bin"])
    (work / "asset.bin").write_bytes(b"not-a-pointer\n")
    commit = commit_all(work, "add lfs asset")
    remote = make_bare_remote(work, tmp_path / "lfs-remote.git")
    return {
        "remote": remote,
        "commit": commit,
    }
