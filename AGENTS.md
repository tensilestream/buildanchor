# Agent Guidelines
<!-- BuildAnchor Rules Block -->
## Build and test commands (BuildAnchor)

Run the tests with:

```bash
uv run pytest
```

This command is **declared** — read from a manifest, not yet executed. Run `buildanchor verify` to prove it runs before relying on it.

- **build**: `python -m build`
- **lint**: `ruff check .`
- **format**: `ruff format .`

One project at the root, with subordinate package(s): `sdk/node`. The command above is the root project's.

Before adding a dependency, check whether it is already present:

```bash
buildanchor find --package <name>
```

<!-- End BuildAnchor Rules Block -->

