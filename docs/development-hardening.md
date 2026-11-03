# Archive hardening and offline development

## Delivered: artifact contracts

`archive-artifacts-v1.schema.json` defines identity, receipt, partial, reference,
progress, audit, report and verified JSONL row contracts. Producers validate before
publication and readers reject incompatible versions and unsafe relative paths.
Legacy v1 rows without an explicit version remain supported; additive optional
fields are allowed. Golden synthetic v1 fixtures protect compatibility.
Reports also check status totals and completeness claims against audit evidence.
Quarantine CLI output stays an operation result, while `report.json` remains a
consistent corpus report.

Verification: existing 48-test suite passed before the final semantic contract
checks; all four focused contract tests then passed. No real source was contacted.

Further hardening stages are implemented in separate commits and documented here
as each stage becomes available. The archive acquisition guide remains the
operator entry point.

## Delivered: bounded validators and crash recovery

PDF/ZIP/text/media checks execute in a separate Python process with a default
30-second wall/CPU budget and 512 MiB resident-memory cap. Linux also enforces an
address-space limit; macOS monitors child RSS. A worker has a parent-death watcher,
wall alarm, no bytecode writes and no permission to create regular-file output.
The parent terminates and reaps the worker on timeout, memory excess, storage
failure or operator interruption. Limits are configurable in downloader settings.
These are process/resource controls, not an OS network/filesystem sandbox.

SIGTERM/Ctrl-C stop the CLI with a resumable pause. Abrupt exit tests cover six
durability boundaries, including partial checkpoint, receipt, original rename,
references, ledger commit and audit publication. An unreceipted complete partial
restarts safely rather than requesting a range beyond EOF. Originals are never
silently overwritten. Five focused tests passed, including actual child memory
allocation and forced subprocess exits; prior unrelated regression checks passed.
