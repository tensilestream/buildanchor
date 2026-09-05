# Changelog

All notable changes to BuildAnchor are documented here.

## [0.3.1] - 2026-09-05

### Added

- **Monorepo Topology Intelligence**: Automatic discovery and component mapping across 8 major ecosystems (pnpm, npm/yarn workspaces, Cargo, Gradle multi-project, Maven multi-module, Go multi-module, Nx, and Turborepo).
- **Semantic Component Categorization**: Auto-classifies monorepo packages into `ui`, `backend`, and `shared` scopes.
- **Scoped Command Resolution**: Execute targeted tests and builds via `buildanchor cmd --scope <ui|backend|shared|module>` or `--changed [baseline]`.
- **Monorepo Inspection Tools**: Added `buildanchor modules` CLI command and `build.modules` MCP tool for AI coding agents.
- **Automated CI/CD Release Pipeline**: Added GitHub Actions workflow (`.github/workflows/release.yml`) for automated testing, artifact generation, PyPI publishing, and GitHub Releases with standardized release notes.

## [0.2.0] - 2026-09-05

### Added

- Copyright, attribution, trademark, and GitHub-only support policies with issue templates.
- User-scoped CLI installers for macOS/Linux and Windows, plus a Homebrew formula/tap layout.
- Git repository and baseline diagnostics, including tracked and untracked change impact.
- Optional bounded validation probes through `buildanchor validate-change --execute`.
- Probe exit status, duration, captured output, timeout handling, and unavailable-tool results.
- GitHub/MCP-friendly validation controls and clearer static-mode limitations.
- CI-friendly exit codes for valid, invalid, inconclusive, and blocked results.

## [0.1.0] - 2026-09-03

### Added

- Static Build Truth inspection for Maven, Gradle, Node, Python, Go, Rust, .NET, and generic build markers.
- Evidence-linked `v1` report, compact context pack, and change-impact models.
- CLI, MCP stdio, and HTTP interfaces.
- Inspect, context, change-impact, validate-change, repair, and dependency explanation operations.
- Python synchronous and asynchronous SDK clients.
- Java 17 dependency-free SDK with local and HTTP transports.
- Initial tests and open-source repository governance files.

### Limitations

- This release does not claim dependency resolution or test success in static mode.
- Live sandboxed resolution and Harness integration are planned next.
