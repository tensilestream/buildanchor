# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""What shape is this repository?

"Monorepo: 1 module detected" is the kind of line that makes an agent discount
everything around it. A repository with a root project and one SDK subdirectory
is not a monorepo, and telling an agent to reach for `--scope ui` there is noise
at best and a wrong turn at worst.

Three shapes, distinguished because each deserves different advice:

``single-project``
    One project, at the root. There is one command and no scoping decision to
    make. The right thing to say is the command, and then nothing.

``root-plus-satellites``
    A root project with subordinate packages beside it — an SDK, an example, a
    tooling directory. The root command is the default; the satellites are
    addressable but rarely what the caller wants.

``monorepo``
    Several sibling projects, or a declared workspace. Scoping is the point,
    and running everything is the expensive mistake worth warning about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .vocabulary import PROJECT_MARKER_NAMES

SINGLE_PROJECT = "single-project"
ROOT_PLUS_SATELLITES = "root-plus-satellites"
MONOREPO = "monorepo"
UNKNOWN = "unknown"

#: Files that declare a workspace outright, whatever the directory layout.
WORKSPACE_DECLARATIONS: tuple[str, ...] = (
    "pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json", "go.work",
)

#: Manifests that make the repository root a project in its own right — the
#: same set every other surface calls a project marker.
ROOT_PROJECT_MANIFESTS: tuple[str, ...] = PROJECT_MARKER_NAMES


def detect(workspace: Path, modules: list[dict[str, Any]], read: Any = None) -> dict[str, Any]:
    """Classify the repository and explain the classification.

    ``read`` is an optional text reader used to look inside root manifests; when
    absent the classification falls back to file presence alone.
    """
    declarations = [name for name in WORKSPACE_DECLARATIONS if (workspace / name).is_file()]
    if _declares_npm_workspaces(workspace, read):
        declarations.append("package.json [workspaces]")
    if _declares_cargo_workspace(workspace, read):
        declarations.append("Cargo.toml [workspace]")
    if _declares_maven_modules(workspace, read):
        declarations.append("pom.xml [modules]")

    root_manifests = [name for name in ROOT_PROJECT_MANIFESTS if (workspace / name).is_file()]
    module_paths = [module.get("path", ".") for module in modules]
    non_root_modules = [path for path in module_paths if path not in ("", ".")]

    if declarations:
        return _result(MONOREPO, f"workspace declared by {', '.join(sorted(set(declarations)))}",
                       root_manifests, non_root_modules)

    if not non_root_modules:
        if root_manifests:
            return _result(SINGLE_PROJECT, f"one project at the root ({root_manifests[0]})",
                           root_manifests, non_root_modules)
        return _result(UNKNOWN, "no project manifest was found", root_manifests, non_root_modules)

    if root_manifests:
        count = len(non_root_modules)
        return _result(
            ROOT_PLUS_SATELLITES,
            f"a root project ({root_manifests[0]}) with {count} subordinate "
            f"package{'s' if count != 1 else ''}",
            root_manifests, non_root_modules,
        )

    if len(non_root_modules) == 1:
        return _result(SINGLE_PROJECT, f"one project, at {non_root_modules[0]}",
                       root_manifests, non_root_modules)

    return _result(MONOREPO, f"{len(non_root_modules)} sibling projects with no root project",
                   root_manifests, non_root_modules)


def _result(shape: str, reason: str, root_manifests: list[str], modules: list[str]) -> dict[str, Any]:
    return {
        "shape": shape,
        "reason": reason,
        "is_monorepo": shape == MONOREPO,
        "root_is_a_project": bool(root_manifests),
        "module_count": len(modules),
    }


def _read_text(workspace: Path, name: str, read: Any) -> str:
    path = workspace / name
    if not path.is_file():
        return ""
    if read is not None:
        return read(path)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _declares_npm_workspaces(workspace: Path, read: Any) -> bool:
    import json
    text = _read_text(workspace, "package.json", read)
    if not text:
        return False
    try:
        return bool(json.loads(text).get("workspaces"))
    except (json.JSONDecodeError, AttributeError):
        return False


def _declares_cargo_workspace(workspace: Path, read: Any) -> bool:
    return "[workspace]" in _read_text(workspace, "Cargo.toml", read)


def _declares_maven_modules(workspace: Path, read: Any) -> bool:
    import re
    return bool(re.search(r"<modules>.*?<module>", _read_text(workspace, "pom.xml", read), re.DOTALL))
