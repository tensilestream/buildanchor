# BuildAnchor benchmarks

The benchmark harness measures local latency for the core operations most relevant to an agent
workflow: repository inspection, compact context generation, and planning. It uses a deterministic
small polyglot fixture and the Python standard library, so it does not add a runtime dependency.

Run it from the repository root:

```bash
uv run python benchmarks/benchmark_cli.py --iterations 20 --warmups 3 --format text
```

Use JSON output for CI or comparison tooling:

```bash
uv run python benchmarks/benchmark_cli.py --iterations 20 --warmups 3 --format json
```

These measurements are engineering baselines. They are not a claim that every team or repository
will save a specific amount of time; teams should compare them with their current inspection and
validation workflow.
