from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Manifest:
    version: int
    run_id: str
    repo_url: str
    commit_sha: str
    host: str
    transport: str
    allow_file_urls: bool = False


@dataclass(frozen=True)
class CloneResult:
    resolved_commit: str
    submodules_materialized: bool
    lfs_materialized: bool


@dataclass(frozen=True)
class ServicePaths:
    run_dir: Path
    workspace_dir: Path
    inputs_dir: Path
    outputs_dir: Path
    logs_dir: Path
    original_repo_dir: Path
    source_manifest_path: Path
    report_path: Path
    summary_path: Path
    service_log_path: Path


@dataclass(frozen=True)
class SourceManifest:
    schema_version: str
    run_id: str
    repo_url: str
    requested_commit: str
    resolved_commit: str
    host: str
    transport: str
    submodules_materialized: bool
    lfs_materialized: bool
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RunReport:
    service_schema_version: str
    run_id: str
    status: str
    reason: str | None
    status_detail: str | None
    exit_code: int
    repo_url: str | None
    requested_commit: str | None
    resolved_commit: str | None
    artifact_paths: dict[str, str]
    submodules_materialized: bool
    lfs_materialized: bool
    started_at: str
    finished_at: str
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

