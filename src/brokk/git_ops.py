from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from brokk.models import CloneResult, Manifest


class GitWorkflowError(RuntimeError):
    """Base class for Brokk git workflow failures."""


class CloneFailedError(GitWorkflowError):
    """Repository clone failed."""


class CommitNotFoundError(GitWorkflowError):
    """Requested commit does not exist in the clone."""


class CheckoutMismatchError(GitWorkflowError):
    """Resolved HEAD does not match the requested commit."""


class SubmoduleFailedError(GitWorkflowError):
    """Submodule materialization failed."""


class LfsFailedError(GitWorkflowError):
    """Git LFS materialization failed."""


class GitCommandError(RuntimeError):
    def __init__(self, args: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"command failed ({returncode}): {' '.join(args)}")


def clone_and_materialize_repo(
    manifest: Manifest,
    *,
    workspace_repo: Path,
    logger: logging.Logger,
) -> CloneResult:
    git_env = _build_git_env()
    git_prefix = _git_prefix(manifest.allow_file_urls)

    try:
        run_command(
            git_prefix + ["clone", "--no-checkout", manifest.repo_url, str(workspace_repo)],
            logger=logger,
            env=git_env,
            retries=3,
            timeout=900,
        )
    except GitCommandError as exc:
        raise CloneFailedError(str(exc)) from exc

    if not _has_commit(workspace_repo, manifest.commit_sha, git_env, git_prefix, logger):
        raise CommitNotFoundError(f"requested commit not found: {manifest.commit_sha}")

    run_command(
        git_prefix + ["-C", str(workspace_repo), "checkout", "--detach", manifest.commit_sha],
        logger=logger,
        env=git_env,
        timeout=300,
    )

    resolved_commit = (
        run_command(
            ["git", "-C", str(workspace_repo), "rev-parse", "HEAD"],
            logger=logger,
            env=git_env,
            timeout=60,
        )
        .stdout.strip()
        .lower()
    )
    if resolved_commit != manifest.commit_sha:
        raise CheckoutMismatchError(
            f"resolved HEAD {resolved_commit} does not match requested {manifest.commit_sha}"
        )

    try:
        run_command(
            git_prefix + ["-C", str(workspace_repo), "submodule", "sync", "--recursive"],
            logger=logger,
            env=git_env,
            timeout=300,
        )
        run_command(
            git_prefix + [
                "-C",
                str(workspace_repo),
                "submodule",
                "update",
                "--init",
                "--recursive",
            ],
            logger=logger,
            env=git_env,
            retries=3,
            timeout=900,
        )
    except GitCommandError as exc:
        raise SubmoduleFailedError(str(exc)) from exc

    submodule_paths = _list_submodule_paths(workspace_repo, git_env, git_prefix, logger)
    submodules_materialized = bool(submodule_paths)

    lfs_materialized = materialize_lfs(
        workspace_repo,
        submodule_paths,
        logger=logger,
        git_env=git_env,
        git_prefix=git_prefix,
    )

    return CloneResult(
        resolved_commit=resolved_commit,
        submodules_materialized=submodules_materialized,
        lfs_materialized=lfs_materialized,
    )


def run_command(
    args: list[str],
    *,
    logger: logging.Logger,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    retries: int = 1,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    attempt = 0
    while True:
        attempt += 1
        logger.info("running command (attempt %s/%s): %s", attempt, retries, " ".join(args))
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.stdout:
            logger.info("stdout:\n%s", completed.stdout.rstrip())
        if completed.stderr:
            logger.info("stderr:\n%s", completed.stderr.rstrip())
        if completed.returncode == 0:
            return completed

        if attempt >= retries:
            raise GitCommandError(args, completed.returncode, completed.stdout, completed.stderr)

        time.sleep(2 ** (attempt - 1))


def materialize_lfs(
    workspace_repo: Path,
    submodule_paths: list[Path],
    *,
    logger: logging.Logger,
    git_env: dict[str, str],
    git_prefix: list[str],
) -> bool:
    repo_paths = [workspace_repo, *submodule_paths]
    lfs_required = any(repo_uses_lfs(repo_path) for repo_path in repo_paths)

    if not lfs_required:
        return False
    if not git_lfs_available():
        raise LfsFailedError("git-lfs is required but not available on PATH")

    for repo_path in repo_paths:
        try:
            run_command(
                ["git", "-C", str(repo_path), "lfs", "install", "--local", "--skip-smudge"],
                logger=logger,
                env=git_env,
                timeout=120,
            )
            run_command(
                git_prefix + ["-C", str(repo_path), "lfs", "pull"],
                logger=logger,
                env=git_env,
                retries=3,
                timeout=900,
            )
        except GitCommandError as exc:
            raise LfsFailedError(f"git-lfs materialization failed in {repo_path}: {exc}") from exc

    remaining_pointers = [
        str(repo_path) for repo_path in repo_paths if repo_has_lfs_pointers(repo_path)
    ]
    if remaining_pointers:
        raise LfsFailedError(
            f"git-lfs pointer files remain after pull: {', '.join(sorted(remaining_pointers))}"
        )

    return True


def repo_uses_lfs(repo_path: Path) -> bool:
    for attributes_path in repo_path.rglob(".gitattributes"):
        if ".git" in attributes_path.parts:
            continue
        try:
            if "filter=lfs" in attributes_path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return repo_has_lfs_pointers(repo_path)


def repo_has_lfs_pointers(repo_path: Path) -> bool:
    if not repo_path.exists():
        return False

    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-z"],
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode != 0:
        return False

    for raw_path in result.stdout.split(b"\x00"):
        if not raw_path:
            continue
        path = repo_path / raw_path.decode("utf-8", errors="ignore")
        try:
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                sample = handle.read(256)
        except OSError:
            continue
        if sample.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            return True

    return False


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


def _build_git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    return env


def _git_prefix(allow_file_urls: bool) -> list[str]:
    if allow_file_urls:
        return ["git", "-c", "protocol.file.allow=always"]
    return ["git"]


def _has_commit(
    repo_path: Path,
    commit_sha: str,
    git_env: dict[str, str],
    git_prefix: list[str],
    logger: logging.Logger,
) -> bool:
    try:
        run_command(
            git_prefix + ["-C", str(repo_path), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
            logger=logger,
            env=git_env,
            timeout=60,
        )
        return True
    except GitCommandError:
        return False


def _list_submodule_paths(
    repo_path: Path,
    git_env: dict[str, str],
    git_prefix: list[str],
    logger: logging.Logger,
) -> list[Path]:
    result = run_command(
        git_prefix + ["-C", str(repo_path), "submodule", "status", "--recursive"],
        logger=logger,
        env=git_env,
        timeout=300,
    )

    submodule_paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        submodule_paths.append(repo_path / parts[1])
    return submodule_paths
