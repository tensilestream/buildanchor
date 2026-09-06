# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Manifest parsing shared by the ecosystem adapters.

One parser, used by every caller. The previous approach scanned a whole
``pyproject.toml`` with a single greedy regex, which silently merged every entry
of a single-line array into one coordinate:

    dev = ["pytest>=7", "httpx>=0.25.0"]
      ->  'pytest>=7", "httpx>=0.25.0'

The trigger was the layout of the array, not the section it sat in, so the same
fault reached ``[project] dependencies`` whenever it was written on one line.
"""

from __future__ import annotations

import re

try:  # Python 3.11+
    import tomllib as _tomllib
except ImportError:  # pragma: no cover - exercised on 3.10
    _tomllib = None

_STRING_LITERAL = re.compile(r"""(?P<quote>['"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)""")
_VERSIONED = re.compile(r"[<>=!~^]")


def _string_items(array_body: str) -> list[str]:
    """Return every quoted item in an array literal, one entry per element.

    Non-greedy and quote-aware, so single-line and multi-line arrays parse the
    same way.
    """
    return [match.group("value") for match in _STRING_LITERAL.finditer(array_body)]


def _array_after(text: str, key: str) -> str | None:
    """Return the raw body of the ``key = [...]`` array, honouring nesting."""
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*\[", text)
    if not match:
        return None
    start = match.end()
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return None


def python_dependencies(text: str) -> list[tuple[str, str]]:
    """Return ``(coordinate, scope)`` for every declared Python dependency.

    ``scope`` is ``"dependencies"`` for the main array and the extra's name for
    entries under ``[project.optional-dependencies]``.
    """
    if _tomllib is not None:
        try:
            data = _tomllib.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            return _dependencies_from_parsed(data)
    return _dependencies_from_text(text)


def _dependencies_from_parsed(data: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    project = data.get("project")
    if isinstance(project, dict):
        for item in project.get("dependencies", []) or []:
            if isinstance(item, str):
                found.append((item, "dependencies"))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for extra, items in optional.items():
                for item in items or []:
                    if isinstance(item, str):
                        found.append((item, str(extra)))
    # Poetry keeps its dependencies elsewhere; read them rather than miss them.
    poetry = data.get("tool", {}).get("poetry") if isinstance(data.get("tool"), dict) else None
    if isinstance(poetry, dict):
        for section, scope in (("dependencies", "dependencies"), ("dev-dependencies", "dev")):
            entries = poetry.get(section)
            if not isinstance(entries, dict):
                continue
            for name, constraint in entries.items():
                if name.lower() == "python":
                    continue
                if isinstance(constraint, str):
                    found.append((f"{name}{constraint}", scope))
                else:
                    found.append((str(name), scope))
    return found


def _dependencies_from_text(text: str) -> list[tuple[str, str]]:
    """Fallback for interpreters without ``tomllib``, or unparseable TOML."""
    found: list[tuple[str, str]] = []
    body = _array_after(text, "dependencies")
    if body is not None:
        found.extend((item, "dependencies") for item in _string_items(body))

    section = re.search(r"(?ms)^\[project\.optional-dependencies\]\s*(.*?)(?=^\[|\Z)", text)
    if section:
        for line in re.finditer(r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*\[", section.group(1)):
            extra = line.group(1)
            extra_body = _array_after(section.group(1), extra)
            if extra_body is not None:
                found.extend((item, extra) for item in _string_items(extra_body))
    return found


def requires_python(text: str) -> str | None:
    """Return the declared ``requires-python`` constraint, if any."""
    if _tomllib is not None:
        try:
            data = _tomllib.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            value = data.get("project", {}).get("requires-python") if isinstance(data.get("project"), dict) else None
            if isinstance(value, str):
                return value
    match = re.search(r"""requires-python\s*=\s*['"]([^'"]+)['"]""", text)
    return match.group(1) if match else None


def looks_versioned(coordinate: str) -> bool:
    """Whether a coordinate carries a version constraint."""
    return bool(_VERSIONED.search(coordinate))


def is_parseable(text: str) -> bool:
    """Whether a pyproject.toml parses.

    Without ``tomllib`` there is no way to be sure, so this answers ``True``
    rather than reporting a problem it cannot actually see.
    """
    if _tomllib is None:
        return True
    try:
        _tomllib.loads(text)
    except Exception:
        return False
    return True
