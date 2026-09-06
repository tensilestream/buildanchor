# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""One fact, one place.

Nearly every defect found late in this project was the same shape: the same
knowledge written down twice, drifting apart where nobody was looking.

* ``is_monorepo`` was derived three times with three slightly different rules.
* ``modules`` returned a bare array from the CLI and an envelope everywhere else.
* Two phase-alias tables disagreed, so a ``justfile`` target named ``unit`` was
  found and an npm script named ``unit`` was not.
* ``build.gradle`` was a project marker for ``doctor`` but not for the evidence
  invariant, leaving a Gradle-shaped hole in a guarantee.
* Two tools answered the same question differently.
* The SDKs implemented three different subsets of the product.

None was hard to fix. All were hard to *see*. These tests fail when a second
copy appears, which is the only reliable way to keep it from happening again.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src" / "buildanchor"


def _sources() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(SOURCE_ROOT.rglob("*.py"))]


class VocabularyTests(unittest.TestCase):
    """Marker names and phase aliases are defined once."""

    def test_project_markers_are_defined_once(self) -> None:
        definitions = [
            str(path.relative_to(SOURCE_ROOT))
            for path, source in _sources()
            if re.search(r"(?m)^\s*PROJECT_MARKERS(_NAMES)?\s*[:=]\s*(dict|tuple|\{|\()", source)
        ]
        self.assertEqual(
            definitions, ["build_truth/core/vocabulary.py"],
            "project markers are defined in more than one place; they will drift",
        )

    def test_phase_aliases_are_defined_once(self) -> None:
        definitions = [
            str(path.relative_to(SOURCE_ROOT))
            for path, source in _sources()
            if re.search(r"(?mi)^\s*(PHASE_ALIASES|phase_aliases)\s*[:=]\s*(dict|\{)", source)
        ]
        self.assertEqual(
            definitions, ["build_truth/core/vocabulary.py"],
            "phase aliases are defined in more than one place; they will drift",
        )

    def test_every_marker_maps_to_a_known_ecosystem(self) -> None:
        from buildanchor.build_truth.core.build_systems import MARKERS
        from buildanchor.build_truth.core.vocabulary import PROJECT_MARKERS
        known = {system for system, _markers, _languages in MARKERS}
        for marker, ecosystem in PROJECT_MARKERS.items():
            self.assertIn(ecosystem, known, f"{marker} claims unknown ecosystem {ecosystem}")

    def test_the_evidence_invariant_covers_every_marker(self) -> None:
        """A marker doctor explains but the invariant ignores is a silent hole."""
        from buildanchor.build_truth.core.repository_shape import ROOT_PROJECT_MANIFESTS
        from buildanchor.build_truth.core.vocabulary import PROJECT_MARKER_NAMES
        from buildanchor.build_truth.features.diagnostics import PROJECT_MARKERS as diagnosed
        from buildanchor.engine import BuildAnchor
        self.assertEqual(set(BuildAnchor.PROJECT_MARKERS), set(PROJECT_MARKER_NAMES))
        self.assertEqual(set(diagnosed), set(PROJECT_MARKER_NAMES))
        self.assertEqual(set(ROOT_PROJECT_MANIFESTS), set(PROJECT_MARKER_NAMES))

    def test_no_phase_resolves_to_a_publishing_task(self) -> None:
        """Running `release` because somebody asked to build is unforgivable."""
        from buildanchor.build_truth.core.vocabulary import NEVER_RUN, PHASE_ALIASES, aliases_for
        for phase in PHASE_ALIASES:
            self.assertFalse(
                set(aliases_for(phase)) & NEVER_RUN,
                f"phase '{phase}' can resolve to a publishing task",
            )

    def test_both_command_paths_accept_the_same_names(self) -> None:
        """A justfile target and an npm script of the same name must both work."""
        import json
        import tempfile

        from buildanchor.engine import BuildAnchor
        for name in ("unit", "tests", "test:all"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "justfile").write_text(f"{name}:\n    echo hi\n", encoding="utf-8")
                from_runner = BuildAnchor(str(root)).resolve_command("test")["command"]

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "package.json").write_text(
                    json.dumps({"name": "x", "scripts": {name: "node --test"}}), encoding="utf-8")
                from_scripts = BuildAnchor(str(root)).resolve_command("test")["command"]

            self.assertIsNotNone(from_runner, f"justfile target '{name}' was not found")
            self.assertIsNotNone(from_scripts, f"npm script '{name}' was not found")


class IgnoreListTests(unittest.TestCase):
    def test_the_ignore_list_is_defined_once(self) -> None:
        definitions = [
            str(path.relative_to(SOURCE_ROOT))
            for path, source in _sources()
            if re.search(r'(?m)^\s*(IGNORED_DIRS|ignored)\s*[:=]\s*(frozenset|set|\{)', source)
        ]
        self.assertEqual(
            definitions, ["build_truth/features/inspection.py"],
            "the ignored-directory list is defined in more than one place",
        )


class ShapeDerivationTests(unittest.TestCase):
    """``is_monorepo`` is decided once and read everywhere."""

    def test_is_monorepo_is_never_re_derived(self) -> None:
        offenders = [
            str(path.relative_to(SOURCE_ROOT))
            for path, source in _sources()
            if path.name != "repository_shape.py"
            and re.search(r'"is_monorepo":\s*(len\(|bool\(len\()', source)
        ]
        self.assertEqual(
            offenders, [],
            "is_monorepo is being computed from module counts instead of the report",
        )

    def test_every_surface_agrees(self) -> None:
        import json
        import tempfile

        from buildanchor.engine import BuildAnchor
        from buildanchor.sdk import BuildAnchorClient
        from buildanchor.transports import MCPServer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "a" / "package.json").write_text(
                json.dumps({"name": "a", "scripts": {"test": "node --test"}}), encoding="utf-8")
            (root / "b").mkdir()
            (root / "b" / "package.json").write_text(
                json.dumps({"name": "b", "scripts": {"test": "node --test"}}), encoding="utf-8")

            report = BuildAnchor(str(root)).inspect()
            sdk = BuildAnchorClient(workspace=str(root)).modules()["is_monorepo"]
            mcp = MCPServer(str(root)).call_tool("build.modules", {})["is_monorepo"]
            cmd = BuildAnchor(str(root)).resolve_command("test")["is_monorepo"]
            self.assertEqual({report.repository["is_monorepo"], sdk, mcp, cmd}, {True})


class ContradictionTests(unittest.TestCase):
    """Two answers to the same question is the defect that costs the most trust."""

    def test_the_injected_block_agrees_with_the_command_tool(self) -> None:
        import tempfile

        from buildanchor.engine import BuildAnchor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "x"\nversion = "1"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
            engine = BuildAnchor(str(root))
            resolved = engine.resolve_command("test")["command"]
            self.assertIn(resolved, engine.llm_prompt().content)

    def test_doctor_agrees_with_the_report(self) -> None:
        import json
        import tempfile

        from buildanchor.engine import BuildAnchor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "web").mkdir()
            (root / "web" / "package.json").write_text(
                json.dumps({"name": "web", "scripts": {"test": "node --test"}}), encoding="utf-8")
            engine = BuildAnchor(str(root))
            diagnosis = engine.diagnose()
            report = engine.inspect()
            self.assertEqual(
                {m["path"] for m in diagnosis["modules"]},
                {m["path"] for m in report.module_details},
            )
            self.assertEqual(diagnosis["repository"], report.repository)


if __name__ == "__main__":
    unittest.main()


class CompatibilityChartTests(unittest.TestCase):
    """The README's support matrix is generated, not written.

    A hand-maintained chart is stale the moment someone adds a probe and forgets
    the README — and a support claim that is not true is worse than no chart.
    """

    def test_the_chart_matches_the_code(self) -> None:
        import subprocess
        import sys as _sys
        result = subprocess.run(
            [_sys.executable, str(REPO_ROOT / "scripts" / "generate_compatibility.py"), "--check"],
            capture_output=True, text=True, check=False, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_ecosystem_the_code_supports_appears(self) -> None:
        from buildanchor.build_truth.core.build_systems import ECOSYSTEM_LABELS, MARKERS
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for system, _markers, _languages in MARKERS:
            if system == "generic":
                continue
            self.assertIn(ECOSYSTEM_LABELS[system], readme,
                          f"{system} is supported but absent from the compatibility chart")

    def test_every_task_runner_appears(self) -> None:
        from buildanchor.build_truth.core import conventions
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for runner in conventions._RUNNERS:
            self.assertIn(runner["files"][0], readme,
                          f"{runner['name']} is supported but absent from the chart")

    def test_every_agent_dialect_appears(self) -> None:
        from buildanchor import agent
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for dialect in agent.FORMATS:
            self.assertIn(f"`{dialect}`", readme,
                          f"the {dialect} dialect is supported but absent from the chart")
