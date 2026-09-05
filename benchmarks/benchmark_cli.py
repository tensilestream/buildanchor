"""Small dependency-free latency benchmark for core BuildAnchor operations."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from typing import Callable

from buildanchor import BuildAnchor


def _create_fixture(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'benchmark-fixture'\nrequires-python = '>=3.10'\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"name":"benchmark-fixture","scripts":{"test":"npm test"}}\n', encoding="utf-8")
    (root / "Makefile").write_text("test:\n\t@echo test\n", encoding="utf-8")
    source = root / "src"
    source.mkdir()
    for index in range(24):
        (source / f"module_{index}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")


def _measure(name: str, operation: Callable[[], object], iterations: int, warmups: int) -> dict[str, object]:
    for _ in range(warmups):
        operation()
    samples_ms = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples_ms.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "operation": name,
        "iterations": iterations,
        "mean_ms": round(mean(samples_ms), 3),
        "median_ms": round(median(samples_ms), 3),
        "p95_ms": round(ordered[p95_index], 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()
    if args.iterations < 1 or args.warmups < 0:
        parser.error("--iterations must be positive and --warmups cannot be negative")

    with TemporaryDirectory(prefix="buildanchor-benchmark-") as directory:
        root = Path(directory)
        _create_fixture(root)
        engine = BuildAnchor(root)
        report = engine.inspect()
        results = [
            _measure("inspect", engine.inspect, args.iterations, args.warmups),
            _measure("context", lambda: engine.context(report, 2500), args.iterations, args.warmups),
            _measure("plan", lambda: engine.plan("Add a validated feature", 2500), args.iterations, args.warmups),
        ]

    if args.format == "json":
        print(json.dumps({"fixture": "small-polyglot-repository", "results": results}, indent=2, sort_keys=True))
    else:
        print("BuildAnchor local benchmark (small-polyglot-repository)")
        for result in results:
            print(
                f"{result['operation']}: median={result['median_ms']} ms "
                f"mean={result['mean_ms']} ms p95={result['p95_ms']} ms "
                f"({result['iterations']} iterations)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
