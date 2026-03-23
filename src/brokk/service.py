from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from brokk.export_tree import export_clean_tree
from brokk.git_ops import (
    CheckoutMismatchError,
    CloneFailedError,
    CommitNotFoundError,
    LfsFailedError,
    SubmoduleFailedError,
    clone_and_materialize_repo,
)
from brokk.manifest import CommitShaError, ManifestError, RepoUrlError, load_manifest
from brokk.models import CloneResult, RunReport, ServicePaths, SourceManifest

SERVICE_SCHEMA_VERSION = "brokk_service_report.v1"
SOURCE_MANIFEST_SCHEMA_VERSION = "brokk_source_manifest.v1"


@dataclass(frozen=True)
class ServiceFailure(Exception):
    status: str
    reason: str
    status_detail: str
    message: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Brokk clone service")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)

    run_dir = Path(os.environ.get("BROKK_RUN_DIR", "/run"))
    manifest_path = Path(
        os.environ.get("BROKK_MANIFEST", str(run_dir / "config" / "manifest.yaml"))
    )
    allow_file_urls = os.environ.get("BROKK_ALLOW_FILE_URLS", "").lower() in {"1", "true", "yes"}
    started_at = timestamp_utc()
    fallback_run_id = compact_run_id()
    paths = build_service_paths(run_dir)

    try:
        prepare_reporting_runtime(paths)
    except OSError as exc:
        print(
            f"error: {run_dir} is not writable; cannot emit service report ({exc})",
            file=sys.stderr,
        )
        return 1

    logger = configure_logger(paths.service_log_path)
    logger.info("brokk service starting")
    logger.info("manifest path: %s", manifest_path)
    logger.info("run dir: %s", run_dir)

    run_id = fallback_run_id
    repo_url: str | None = None
    commit_sha: str | None = None
    resolved_commit: str | None = None
    submodules_materialized = False
    lfs_materialized = False

    try:
        ensure_runtime_layout(paths)
        ensure_file_handler(logger, paths.service_log_path)

        manifest = load_manifest(
            manifest_path,
            default_run_id=fallback_run_id,
            allow_file_urls=allow_file_urls,
        )
        run_id = manifest.run_id
        repo_url = manifest.repo_url
        commit_sha = manifest.commit_sha

        logger.info("run_id: %s", manifest.run_id)
        logger.info("repo_url: %s", manifest.repo_url)
        logger.info("commit_sha: %s", manifest.commit_sha)

        clone_result = perform_clone(paths, manifest, logger)
        resolved_commit = clone_result.resolved_commit
        submodules_materialized = clone_result.submodules_materialized
        lfs_materialized = clone_result.lfs_materialized

        finished_at = timestamp_utc()
        report = RunReport(
            service_schema_version=SERVICE_SCHEMA_VERSION,
            run_id=manifest.run_id,
            status="passed",
            reason=None,
            status_detail=None,
            exit_code=0,
            repo_url=manifest.repo_url,
            requested_commit=manifest.commit_sha,
            resolved_commit=clone_result.resolved_commit,
            artifact_paths=artifact_paths(paths),
            submodules_materialized=submodules_materialized,
            lfs_materialized=lfs_materialized,
            started_at=started_at,
            finished_at=finished_at,
            message=None,
        )
        try:
            write_source_manifest(
                paths.source_manifest_path,
                run_id=manifest.run_id,
                repo_url=manifest.repo_url,
                requested_commit=manifest.commit_sha,
                resolved_commit=clone_result.resolved_commit,
                host=manifest.host,
                transport=manifest.transport,
                submodules_materialized=submodules_materialized,
                lfs_materialized=lfs_materialized,
                started_at=started_at,
                finished_at=finished_at,
            )
        except OSError as exc:
            raise ServiceFailure(
                "error",
                "run-dir-not-writable",
                "source_manifest_write_failed",
                f"failed to write source manifest: {exc}",
            ) from exc

        try:
            write_summary(paths.summary_path, report)
        except OSError as exc:
            failure = ServiceFailure(
                "error",
                "report-write-failed",
                "summary_write_failed",
                f"failed to write summary: {exc}",
            )
            failure_report = RunReport(
                service_schema_version=SERVICE_SCHEMA_VERSION,
                run_id=manifest.run_id,
                status=failure.status,
                reason=failure.reason,
                status_detail=failure.status_detail,
                exit_code=0,
                repo_url=manifest.repo_url,
                requested_commit=manifest.commit_sha,
                resolved_commit=clone_result.resolved_commit,
                artifact_paths=artifact_paths(paths),
                submodules_materialized=submodules_materialized,
                lfs_materialized=lfs_materialized,
                started_at=started_at,
                finished_at=timestamp_utc(),
                message=failure.message,
            )
            return emit_failure_report(paths, failure_report, logger)

        try:
            write_report(paths.report_path, report)
        except OSError as exc:
            logger.error("failed to write report: %s", exc)
            return 1
        cleanup_workspace_repo(paths, logger)
        return 0
    except ManifestError as exc:
        failure = classify_manifest_error(exc)
        logger.error("%s", failure.message)
    except CloneFailedError as exc:
        failure = ServiceFailure("failed", "clone-failed", "clone_failed", str(exc))
        logger.error("%s", failure.message)
    except CommitNotFoundError as exc:
        failure = ServiceFailure("failed", "commit-not-found", "missing_commit", str(exc))
        logger.error("%s", failure.message)
    except CheckoutMismatchError as exc:
        failure = ServiceFailure("failed", "checkout-mismatch", "checkout_mismatch", str(exc))
        logger.error("%s", failure.message)
    except SubmoduleFailedError as exc:
        failure = ServiceFailure("failed", "submodule-failed", "submodule_failed", str(exc))
        logger.error("%s", failure.message)
    except LfsFailedError as exc:
        failure = ServiceFailure("failed", "lfs-failed", "lfs_failed", str(exc))
        logger.error("%s", failure.message)
    except ServiceFailure as exc:
        failure = exc
        logger.error("%s", failure.message)
    except Exception as exc:  # pragma: no cover - defensive fallback
        failure = ServiceFailure("error", "clone-failed", "unexpected_error", str(exc))
        logger.exception("unexpected Brokk failure")

    report = RunReport(
        service_schema_version=SERVICE_SCHEMA_VERSION,
        run_id=run_id,
        status=failure.status,
        reason=failure.reason,
        status_detail=failure.status_detail,
        exit_code=0,
        repo_url=repo_url,
        requested_commit=commit_sha,
        resolved_commit=resolved_commit,
        artifact_paths=artifact_paths(paths),
        submodules_materialized=submodules_materialized,
        lfs_materialized=lfs_materialized,
        started_at=started_at,
        finished_at=timestamp_utc(),
        message=failure.message,
    )
    return emit_failure_report(paths, report, logger)


def build_service_paths(run_dir: Path) -> ServicePaths:
    return ServicePaths(
        run_dir=run_dir,
        workspace_dir=run_dir / "workspace",
        inputs_dir=run_dir / "inputs",
        outputs_dir=run_dir / "outputs",
        logs_dir=run_dir / "artifacts" / "brokk" / "logs",
        original_repo_dir=run_dir / "artifacts" / "original-repo",
        source_manifest_path=run_dir / "inputs" / "source-manifest.json",
        report_path=run_dir / "outputs" / "run_report.json",
        summary_path=run_dir / "outputs" / "summary.md",
        service_log_path=run_dir / "artifacts" / "brokk" / "logs" / "service.log",
    )


def prepare_reporting_runtime(paths: ServicePaths) -> None:
    prepare_directory(paths.outputs_dir)


def ensure_runtime_layout(paths: ServicePaths) -> None:
    for directory in (
        paths.inputs_dir,
        paths.workspace_dir,
        paths.original_repo_dir,
        paths.logs_dir,
    ):
        try:
            prepare_directory(directory)
        except OSError as exc:
            raise ServiceFailure(
                "error",
                "run-dir-not-writable",
                "run_dir_not_writable",
                f"failed to prepare runtime directory {directory}: {exc}",
            ) from exc


def prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe_writable(path)


def probe_writable(path: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix=".brokk-probe-", dir=path, delete=True):
        pass


def configure_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("brokk")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(log_formatter())
    logger.addHandler(stream_handler)

    ensure_file_handler(logger, log_path)

    return logger


def ensure_file_handler(logger: logging.Logger, log_path: Path) -> None:
    resolved_path = log_path.resolve()
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved_path:
            return

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError as exc:
        logger.warning("service log file unavailable at %s: %s", log_path, exc)
        return

    file_handler.setFormatter(log_formatter())
    logger.addHandler(file_handler)


def log_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s %(message)s")


def perform_clone(paths: ServicePaths, manifest, logger: logging.Logger) -> CloneResult:
    workspace_repo = paths.workspace_dir / "repo"
    clone_result = clone_and_materialize_repo(
        manifest,
        workspace_repo=workspace_repo,
        logger=logger,
    )
    export_clean_tree(workspace_repo, paths.original_repo_dir)
    return clone_result


def write_source_manifest(
    path: Path,
    *,
    run_id: str,
    repo_url: str,
    requested_commit: str,
    resolved_commit: str,
    host: str,
    transport: str,
    submodules_materialized: bool,
    lfs_materialized: bool,
    started_at: str,
    finished_at: str,
) -> None:
    payload = SourceManifest(
        schema_version=SOURCE_MANIFEST_SCHEMA_VERSION,
        run_id=run_id,
        repo_url=repo_url,
        requested_commit=requested_commit,
        resolved_commit=resolved_commit,
        host=host,
        transport=transport,
        submodules_materialized=submodules_materialized,
        lfs_materialized=lfs_materialized,
        started_at=started_at,
        finished_at=finished_at,
    )
    path.write_text(json.dumps(payload.to_dict(), indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, report: RunReport) -> None:
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def write_summary(path: Path, report: RunReport) -> None:
    reason = report.reason or "(none)"
    detail = report.status_detail or "(none)"
    message = report.message or "(none)"
    path.write_text(
        "\n".join(
            [
                "# Brokk Service Run Report",
                "",
                "| Field | Value |",
                "|-------|-------|",
                f"| run_id | {report.run_id} |",
                f"| status | {report.status} |",
                f"| reason | {reason} |",
                f"| status_detail | {detail} |",
                f"| repo_url | {report.repo_url or '(unset)'} |",
                f"| requested_commit | {report.requested_commit or '(unset)'} |",
                f"| resolved_commit | {report.resolved_commit or '(unset)'} |",
                f"| submodules_materialized | {str(report.submodules_materialized).lower()} |",
                f"| lfs_materialized | {str(report.lfs_materialized).lower()} |",
                f"| started_at | {report.started_at} |",
                f"| finished_at | {report.finished_at} |",
                f"| message | {message} |",
                "",
            ]
        ),
        encoding="utf-8",
    )


def emit_failure_report(paths: ServicePaths, report: RunReport, logger: logging.Logger) -> int:
    try:
        write_report(paths.report_path, report)
    except OSError as exc:
        logger.error("failed to write report: %s", exc)
        return 1

    try:
        write_summary(paths.summary_path, report)
    except OSError as exc:
        logger.error("failed to write summary: %s", exc)

    cleanup_workspace_repo(paths, logger)
    return 0


def cleanup_workspace_repo(paths: ServicePaths, logger: logging.Logger) -> None:
    workspace_repo = paths.workspace_dir / "repo"
    if not workspace_repo.exists():
        return
    try:
        shutil.rmtree(workspace_repo)
    except OSError as exc:
        logger.warning("failed to clean workspace repo %s: %s", workspace_repo, exc)
        return

    try:
        paths.workspace_dir.rmdir()
    except OSError:
        pass


def classify_manifest_error(exc: ManifestError) -> ServiceFailure:
    if isinstance(exc, RepoUrlError):
        return ServiceFailure("error", "invalid-repo-url", "invalid_repo_url", str(exc))
    if isinstance(exc, CommitShaError):
        return ServiceFailure("error", "invalid-commit-sha", "invalid_commit_sha", str(exc))
    return ServiceFailure("error", "invalid-manifest", "invalid_manifest", str(exc))


def artifact_paths(paths: ServicePaths) -> dict[str, str]:
    return {
        "source_manifest": str(paths.source_manifest_path),
        "original_repo": str(paths.original_repo_dir),
        "logs_dir": str(paths.logs_dir),
        "service_log": str(paths.service_log_path),
        "summary": str(paths.summary_path),
    }


def timestamp_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
