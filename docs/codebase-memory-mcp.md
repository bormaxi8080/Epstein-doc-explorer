# Codebase Memory MCP

This repository is a separate graph project named `Epstein-doc-explorer`.
Do not use Oriol's graph as evidence for this checkout.

Register with the connected MCP tool:

```json
{"repo_path":"/Users/imaxi/workspace/projects/Epstein-doc-explorer","name":"Epstein-doc-explorer","mode":"fast","persistence":false}
```

Or use the installed CLI, from any working directory:

```bash
python3 scripts/ensure_codebase_memory.py --strict
python3 scripts/ensure_codebase_memory.py --status --strict
```

Enable the tracked best-effort post-commit hook once per checkout:

```bash
git config core.hooksPath .githooks
```

The script derives the repository root from its own location, uses argument
arrays and JSON encoding, and never requires a machine-specific executable path.
`--mode moderate` and `--mode full` are optional deeper refreshes. Git commits
remain successful if the external indexer is absent or fails; `--strict` is for
manual verification. No persistent graph database is committed.

Use `list_projects`, `index_status`, then `search_graph`; follow relationships
with `trace_path`, and read exact symbols with `get_code_snippet`. Check
`check_index_coverage` for changed source paths. A ready project is not proof
that every file was parsed. Literal/config/document searches use `rg` directly;
fallback to native source reads when the graph is insufficient.
