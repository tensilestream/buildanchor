# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Registry of supported ecosystem adapters."""

from .dotnet import DotnetAdapter
from .go import GoAdapter
from .jvm import JvmAdapter
from .node import NodeAdapter
from .python import PythonAdapter
from .rust import RustAdapter

ADAPTERS = {
    "maven": JvmAdapter("maven"),
    "gradle": JvmAdapter("gradle"),
    "node": NodeAdapter(),
    "python": PythonAdapter(),
    "go": GoAdapter(),
    "rust": RustAdapter(),
    "dotnet": DotnetAdapter(),
}


def adapter_for(system: str):
    """Return the authoritative adapter for a supported ecosystem, if any."""
    return ADAPTERS.get(system)


__all__ = ["ADAPTERS", "adapter_for"]
