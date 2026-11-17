# Install and run Epstein Document Explorer

Choose a ready Viewer or the acquisition/OCR workflow. Source code, data releases
and working state are separate. Start with [distribution](distribution.md) and the
publisher-pinned release SHA. Existing source data and historical reports remain
unchanged; no Git LFS pull is needed when using an external release.

## Native setup (macOS/Linux)

Use Python 3.12 and Node 22.12 or newer. Python 3.10 remains supported by the library
for bounded documents; 3.11+ is required for streamed large SQLite text. Set an
existing external root in examples/workspace.json, copied outside Git.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements/build.lock -r requirements/processor.lock
.venv/bin/python -m pip install --no-index --no-deps --no-build-isolation -e .
.venv/bin/explorer-data doctor --workspace /external/workspace.json
.venv/bin/explorer-data init --workspace /external/workspace.json
npm ci
npm --prefix network-ui ci
npm --prefix network-ui run build
```

Set PIP_CACHE_DIR/NPM_CONFIG_CACHE under your external scratch directory if caches
must stay there. Obtain/import a Viewer profile and activate it as documented in
distribution.md. Then point DB_PATH and TAG_CLUSTERS_PATH at the same selected
bundle, set HOST=127.0.0.1, and run `npm run api`. The production UI uses /api on
the same origin. The server opens the database read-only by default. Catalogue,
OCR pages, review state and evidence are available in the UI/API.

## Offline synthetic demonstration

`explorer-data demo --output /external/new-demo` creates one approved SYNTHETIC
text document and no real relationships, downloads or model calls. It is useful
for first installation checks before a Drive link exists. Its database, clusters
and TXT can be used directly as a Viewer bundle. It is not archive processing.

## Docker Viewer

The single Compose file includes local Viewer, optional HTTPS and explicit
processor profiles. Base images are pinned by multi-platform digest. npm lockfiles
and hashed Python requirements pin dependencies. The Viewer image installs only
server dependencies, not the original analysis/ML toolchain.

Copy deploy/environment.example outside Git. Every bind root used by a started profile must
already exist: create_host_path=false prevents silently creating a missing disk.
The release parent contains current pointing inside that same mounted parent.

```bash
docker compose --env-file /external/deployment.env -f deploy/compose.yaml config
docker compose --env-file /external/deployment.env -f deploy/compose.yaml build explorer
docker compose --env-file /external/deployment.env -f deploy/compose.yaml up -d explorer
```

Visit http://127.0.0.1:3001. For a public domain, set EXPLORER_DOMAIN and the matching
HTTPS ALLOWED_ORIGINS, configure DNS/ingress, then add `--profile https` before
`up -d`. Caddy manages certificates when the target host permits it. Real-domain
DNS/TLS must be tested on that host. Protect certificate backups separately.

## Processor container and FCC

The pipeline profile never runs implicitly with Viewer. Its default command is
help, and explicit work still requires --allow-fcc. Mount a workspace owned by
PROCESSOR_UID/GID and use container config paths under /workspace. Example:

```bash
docker compose --env-file /external/deployment.env -f deploy/compose.yaml --profile pipeline build processor
docker compose --env-file /external/deployment.env -f deploy/compose.yaml --profile pipeline run --rm --entrypoint explorer-data processor --help
```

FCC is an independently configured service, not bundled credentials or a paid
fallback. Native macOS workers can use the tested loopback FCC endpoint. Containers
need a reachable verified HTTPS FCC endpoint, or a deliberately configured Linux
host-network run for loopback access. host.docker.internal over plain HTTP is not
silently admitted by the FCC client. Viewer needs no FCC key. Send keys only via
your private environment. Free-only admission and run/lifetime budgets still apply.

## Verify and upgrade

Validate health, statistics, catalogue, known page/evidence and filtered graph
results. Health alone does not certify processing coverage. Validate a new bundle,
activate its database/clusters/TXT together and restart Viewer; old SQLite
connections keep their previous file. Roll back by reactivating the previous
verified bundle and restarting. Original binaries have separate acquisition and catalogue metadata; the source checkout and working secrets are never static roots.

## Verification record (2026-11-17)

The native synthetic tests and a real Linux ARM64 Docker Desktop run passed.
Both digest-pinned images built; the Viewer served production UI, statistics and
one synthetic catalogue document without modifying SQLite. The processor ran
help, component diagnostics and synthetic export with network disabled and a
512 MiB memory limit. The acceptance script removed only its uniquely named
containers, loaded image tags and builder. Build tar files/logs were written to
the external scratch directory. No real archive/model calls were made.

Repeat with `python scripts/check_containers.py --scratch /existing/external/tmp`.
The script reserves 20 GiB on the system disk before building, enforces a 15 GiB
safety floor between commands, and never prunes unrelated Docker resources.
Real-domain TLS and a remote FCC endpoint remain deployment-specific checks.
