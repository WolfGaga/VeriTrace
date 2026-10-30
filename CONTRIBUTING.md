# Contributing to VeriTrace

For a bug, open an issue with the command or interface used, relevant package versions, a minimal reproduction, and a redacted error trace. Never include API keys, private research data or complete environment files.

For a change, explain the problem and resulting behavior, keep the scope focused, and include appropriate validation. Preserve bilingual model instructions and evidence/citation semantics unless the change explicitly targets them. Write human-facing documentation, comments and docstrings in English.

VeriTrace uses Apache 2.0. Submit only contributions you have the right to provide under the project's license, and retain applicable third-party notices. For research and industry collaboration, contact [hz496@cam.ac.uk](mailto:hz496@cam.ac.uk?subject=VeriTrace%20collaboration).

From `VERITRACE/`:

```bash
python -m pip install -e ".[documents,dev]"
python -m pytest
python -m ruff check veritrace tests
```

For React, run `npm ci`, `npm test` and `npm run build` in `VERITRACE/frontend/React/`. See [the testing guide](VERITRACE/tests/README.md) for browser checks and optional dependencies.

Offline tests must not use real provider credentials. Keep generated reports, databases, logs, environment files and installed dependencies out of commits. If a change affects research quality, report the evaluation configuration and results separately from functional tests.
