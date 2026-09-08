# CLAUDE.md — Engineering Conventions

This file governs how work is done in this repository. It takes precedence over
`PROJECT_SPEC.md` whenever the two disagree on conventions (workflow, style, process).
`PROJECT_SPEC.md` is the authority on scope, architecture, and the delivery plan.

## Documentation maintenance

`README.md`, `CHANGELOG.md`, `docs/architecture.md`, and `PROJECT_SPEC.md` must always
describe the state of the project that actually exists. Any change to scope, architecture,
the data model, the CLI surface, or dependencies updates the relevant document(s) **in the
same commit** as the code change. A PR that changes behavior without a documentation update
is incomplete.

The full command reference lives in the
[GitHub wiki](https://github.com/netasservice/nettopo/wiki), which is a separate
repository and cannot be part of the same commit. Instead: a PR that changes the CLI
surface must **list the required wiki edits in its description**, and those edits are
applied when the PR merges. The README keeps only the essentials (what the project is,
installation, quickstart, a command table linking to the wiki).

## Git workflow

- One branch per issue, named `feat/<short-description>` or `fix/<short-description>`.
- Every change lands via a pull request into `main`. Never commit directly to `main`.
- `main` is protected: no direct pushes, PRs require passing CI checks before merge.
- Keep PRs scoped to one issue. If a PR touches a section noted in the OWASP review below,
  say so explicitly in the PR description.

## English-only rule

All code, identifiers, comments, commit messages, PR descriptions, and documentation are
written in English, regardless of the language used to discuss the work.

## Coding principles

> Writing good code is less about making a machine understand your intent and more about
> ensuring other developers can easily read and modify your work later.

Follow these principles in order of priority.

### Architecture (SOLID)

- **Single Responsibility** — each function/class has exactly one reason to change.
- **Open/Closed** — extend behavior without modifying existing code.
- **Dependency Inversion** — depend on abstractions, not concrete implementations.

This is why `nettopo` layers strictly inward (`parsing` → `model`; `views` → `model`;
`render`/`export` → `views` + `model`; `cli` orchestrates) and why ingestion is defined as
a `DataSource` interface: a future live-collection source can be added without touching
parsing, model, or views.

### Simplicity triad

- **DRY** — extract repeated logic into reusable helpers.
- **KISS** — choose the simplest design; avoid clever or over-engineered solutions.
- **YAGNI** — do not write code for assumed future requirements.

### Daily practices

- Descriptive naming: clear, unambiguous identifiers — no single-letter variables.
- PEP 8 formatting throughout the Python codebase (enforced by `ruff format` in CI).
- Comments explain *why*, not *what*. If removing a comment wouldn't confuse a future
  reader, don't write it.
- Wrap external calls (file I/O, subprocess, any future HTTP/DB) in `try`/`except` and
  return meaningful error strings — never let a raw traceback be the only diagnostic.
- Always clean up resources (open files, connections, tasks) in `finally` blocks.

## Security review (mandatory)

Every change is checked against the OWASP Top 10 (2021) as adapted for this project in
`PROJECT_SPEC.md` section 11 — path traversal on `--input`/`--output`, filename
sanitization, no `eval`/`exec`/`os.system` on parsed content, no `pickle` or unsafe YAML
loading, and the zero-network-connections guarantee enforced by
`tests/test_no_network.py`. If a change touches any of these and the mitigation isn't
obvious from the diff, call it out in the PR description.

## Testing

- Every PR keeps CI green: `ruff check`, `ruff format --check`, `mypy`, `pytest --cov`.
- New logic ships with tests in the same PR. Parsers and the grouping fingerprints in
  particular are the highest-value, most test-critical code in the project (see
  `PROJECT_SPEC.md` section 12).
