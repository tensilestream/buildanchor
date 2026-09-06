# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable public facade for BuildAnchor's modular engine implementation.

Consumers continue to import :class:`BuildAnchor` from this module. The
implementation is organised by responsibility in ``build_truth``.
"""

from __future__ import annotations

from pathlib import Path

from .build_truth.core.errors import BuildAnchorError
from .build_truth.features.command_resolution import CommandResolutionMixin
from .build_truth.features.context import ContextMixin
from .build_truth.features.diagnostics import DiagnosticsMixin
from .build_truth.features.inspection import InspectionMixin
from .build_truth.features.package_intelligence import PackageIntelligenceMixin
from .build_truth.features.validation import ValidationMixin
from .build_truth.features.verification import VerificationMixin
from .models import BuildReport


class BuildAnchor(
    InspectionMixin,
    ContextMixin,
    DiagnosticsMixin,
    ValidationMixin,
    VerificationMixin,
    CommandResolutionMixin,
    PackageIntelligenceMixin,
):
    """Safe static Build Truth inspection for a bounded workspace."""

    def __init__(self, workspace: str | Path = ".", allow_root: str | Path | None = None):
        raw = Path(workspace).expanduser().resolve()
        if not raw.is_dir():
            tip = " (tip: use '.' for the current directory, or pass an absolute path)"
            if str(workspace) != str(raw):
                raise BuildAnchorError(
                    f"workspace is not a directory: '{workspace}' → resolved to '{raw}'{tip}"
                )
            raise BuildAnchorError(f"workspace is not a directory: '{workspace}'{tip}")
        self.workspace = raw
        self.allow_root = Path(allow_root or raw).expanduser().resolve()
        self._assert_inside(self.workspace)
        self._report_cache: dict[str, BuildReport] = {}


__all__ = ["BuildAnchor", "BuildAnchorError"]
