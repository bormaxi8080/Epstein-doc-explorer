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
