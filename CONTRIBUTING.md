# Contributing to BuildAnchor

Thank you for contributing. BuildAnchor is designed to be useful to developers and safe to run in automated agent and CI environments.

## Contributor quick start

From a fresh checkout, run the test suite and inspect another repository immediately:

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run python benchmarks/credibility_benchmark.py --format text
uv run buildanchor inspect --workspace /path/to/other-repository --format text
```

If you need `buildanchor` available globally on macOS, use the Homebrew instructions below.

## Before opening a pull request

- Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the security model.
- Keep changes focused and explain the user-facing behavior.
- Add or update fixtures for every adapter behavior change.
- Add tests for new report facts and failure states.
- Do not add telemetry, network access, arbitrary command execution, or credential handling without a design discussion.
- Run the documented `uv` Python tests and Java compilation check.

## Local CLI installation and cross-repository testing

To install the current checkout globally for development, use the platform installer. It installs
the local files and does not clone the repository:

```bash
./scripts/install.sh --local --global
```

On Windows PowerShell:

```powershell
.\scripts\install.ps1 -Local -Global
```

After that, changes to `src/` are available through `buildanchor` without reinstalling. Test
against a different checkout by passing its path (or by changing into it):

```bash
buildanchor inspect --workspace /path/to/other-repository --format text
cd /path/to/other-repository
buildanchor validate-change --workspace . --baseline HEAD --format json
```

Keep the BuildAnchor checkout separate from the repository being inspected. BuildAnchor is
read-only by default, and the target repository controls the detected build and validation
commands.

## Adapter contributions

Adapters should implement the documented lifecycle:

```text
detect → inspect → resolve → validate → explain
```

Static inspection must work without a network connection. Live resolution and execution must expose their capability and policy requirements explicitly.

Every material fact needs evidence. If an adapter cannot prove a fact, return `unknown` or `inconclusive` rather than guessing.

## Pull requests

Pull requests should include:

- a concise problem statement
- implementation and schema impact
- security impact
- test evidence
- documentation updates for public behavior

Maintainers may request changes that improve determinism, portability, evidence quality, or safety.

## Commit and release hygiene

Use clear imperative commit subjects. Releases follow semantic versioning. Public schema changes require a compatibility note in `CHANGELOG.md`.
