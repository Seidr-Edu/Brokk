# Brokk

Brokk is the clone service for the pipeline. It pulls a GitHub repository over
public HTTPS, checks out an exact commit, materializes recursive submodules and
Git LFS content, exports a clean working tree, and emits canonical manifests and
service reports under `/run`.

## Service contract

Mount contract:

- Read-only: `/run/config`
- Writable: `/run`

Manifest path: `/run/config/manifest.yaml`

Manifest v1:

```yaml
version: 1
run_id: 20260311T120000Z__example
repo_url: https://github.com/octocat/Hello-World.git
commit_sha: 7fd1a60b01f91b314f59951e1f75e5f7c5c7f1f9
```

Rules:

- `version` is required and must be `1`
- `run_id` is optional; if absent Brokk generates a UTC compact run id
- `repo_url` must be a public GitHub HTTPS repository URL
- `commit_sha` must be a full 40-character SHA
- unknown top-level keys are rejected

Canonical outputs:

- `/run/inputs/source-manifest.json`
- `/run/artifacts/original-repo/`
- `/run/artifacts/brokk/logs/`
- `/run/outputs/run_report.json`
- `/run/outputs/summary.md`

The exported repo artifact is a clean working tree without `.git` metadata.
Git provenance lives in `source-manifest.json` and `run_report.json`.

## Exit semantics

- Exit `0`: Brokk emitted `run_report.json`, even when cloning failed
- Exit `1`: Brokk could not write the canonical output/report paths

The orchestrator should branch on `run_report.json.status`, not the container
exit code.

## Docker

Build locally:

```bash
docker build -t brokk:local .
```

Run locally:

```bash
docker run --rm \
  -e BROKK_MANIFEST=/run/config/manifest.yaml \
  -v /abs/path/to/config:/run/config:ro \
  -v /abs/path/to/run:/run \
  brokk:local
```

## DigitalOcean example

The orchestrator should pull the published image and stage only `/run/config`
and `/run`.

```bash
docker pull ghcr.io/seidr-edu/brokk:latest

docker run --rm \
  -e BROKK_MANIFEST=/run/config/manifest.yaml \
  -v /srv/pipeline/runs/<runId>/services/brokk/config:/run/config:ro \
  -v /srv/pipeline/runs/<runId>/services/brokk/run:/run \
  ghcr.io/seidr-edu/brokk:latest
```

Consume:

- `runs/<runId>/services/brokk/run/inputs/source-manifest.json`
- `runs/<runId>/services/brokk/run/artifacts/original-repo/`
- `runs/<runId>/services/brokk/run/outputs/run_report.json`

## Tests

Run the pytest suite:

```bash
bash tests/run.sh
```

Container tests skip automatically when Docker is unavailable. Some LFS tests
skip when `git-lfs` is not installed on the host.

## Release

This repo includes:

- semantic-release on `main` and `master`
- GHCR image publishing on release
- multi-arch image builds for `linux/amd64` and `linux/arm64`

