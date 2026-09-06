# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for report correctness.

Each test here pins a defect found by running BuildAnchor against a real
polyglot monorepo, and each fails against the behaviour that shipped in 1.1.6.
"""

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from buildanchor import BuildAnchor
from buildanchor.build_truth.core import languages, manifest_parsing


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class NodeDiscoveryTests(unittest.TestCase):
    """A package.json outside a declared workspace is still a project."""

    def test_root_level_node_projects_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # No root package.json, no pnpm-workspace.yaml, no turbo.json, and
            # no apps/ or packages/ convention — just sibling projects.
            _write(root / "service-a" / "pyproject.toml", '[project]\nname = "service-a"\nversion = "0.1.0"\n')
            _write(root / "web-ui" / "package.json",
                   json.dumps({"name": "web-ui", "scripts": {"test": "vitest run"}}))
            _write(root / "bot" / "package.json",
                   json.dumps({"name": "bot", "scripts": {"test": "vitest run"}}))

            modules = {m.path: m for m in BuildAnchor(str(root)).discover_modules()}
            self.assertIn("web-ui", modules)
            self.assertIn("bot", modules)
            self.assertEqual(modules["web-ui"].ecosystem, "node")

    def test_a_package_without_scripts_is_excluded_with_a_stated_reason(self) -> None:
        """Every marker resolves to a module or to a reason it did not."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "types-only" / "package.json", json.dumps({"name": "types-only"}))
            report = BuildAnchor(str(root)).inspect()
            self.assertNotIn("types-only", [m["path"] for m in report.module_details])
            self.assertTrue(
                any("types-only" in note for note in report.limitations),
                f"exclusion was silent; limitations were {report.limitations}",
            )

    def test_no_project_marker_is_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "svc" / "pyproject.toml", '[project]\nname = "svc"\nversion = "0.1.0"\n')
            _write(root / "web" / "package.json", json.dumps({"name": "web", "scripts": {"test": "vitest"}}))
            _write(root / "quiet" / "package.json", json.dumps({"name": "quiet"}))

            report = BuildAnchor(str(root)).inspect()
            module_dirs = {m["path"] for m in report.module_details} | {"."}
            project_markers = [
                item.path for item in report.evidence
                if Path(item.path).name in ("package.json", "pyproject.toml")
            ]
            for marker in project_markers:
                directory_of = str(Path(marker).parent) or "."
                if directory_of in module_dirs:
                    continue
                self.assertTrue(
                    any(marker in note for note in report.limitations),
                    f"{marker} is neither a module nor explained",
                )


class DependencyParsingTests(unittest.TestCase):
    def test_single_line_array_yields_one_coordinate_per_entry(self) -> None:
        parsed = manifest_parsing.python_dependencies(
            '[project]\nname = "x"\nversion = "1"\n\n'
            '[project.optional-dependencies]\n'
            'dev = ["pytest>=7", "httpx>=0.25.0", "pytest-asyncio>=0.23"]\n'
        )
        self.assertEqual(
            [coordinate for coordinate, _scope in parsed],
            ["pytest>=7", "httpx>=0.25.0", "pytest-asyncio>=0.23"],
        )

    def test_main_dependencies_on_one_line_parse_identically(self) -> None:
        """The trigger was the array's layout, not the section it sat in."""
        parsed = manifest_parsing.python_dependencies(
            '[project]\nname = "x"\nversion = "1"\ndependencies = ["fastapi>=0.1", "sqlalchemy>=2.0"]\n'
        )
        self.assertEqual([c for c, _ in parsed], ["fastapi>=0.1", "sqlalchemy>=2.0"])

    def test_no_coordinate_contains_a_quote_or_separator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml",
                   '[project]\nname = "x"\nversion = "1"\ndependencies = ["a>=1", "b>=2"]\n\n'
                   '[project.optional-dependencies]\ndev = ["c>=3", "d>=4"]\n')
            report = BuildAnchor(str(root)).inspect()
            for item in report.dependencies:
                self.assertNotIn('"', item["coordinate"])
                self.assertNotIn(",", item["coordinate"])

    def test_optional_node_dependencies_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({
                "name": "x", "optionalDependencies": {"fsevents": "^2.3.0"},
            }))
            report = BuildAnchor(str(root)).inspect()
            self.assertIn("fsevents@^2.3.0", [d["coordinate"] for d in report.dependencies])


class MultiManifestTests(unittest.TestCase):
    """Dependencies and facts must not come from one arbitrary manifest."""

    def test_every_module_contributes_its_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 'lib-a' sorts first and declares nothing; under the old
            # first-manifest-wins rule it shadowed every other project.
            _write(root / "lib-a" / "pyproject.toml", '[project]\nname = "lib-a"\nversion = "1"\n')
            _write(root / "service-a" / "pyproject.toml",
                   '[project]\nname = "service-a"\nversion = "1"\ndependencies = ["fastapi>=0.1"]\n')

            report = BuildAnchor(str(root)).inspect()
            coordinates = [d["coordinate"] for d in report.dependencies]
            self.assertIn("fastapi>=0.1", coordinates)
            self.assertEqual(
                {d["module"] for d in report.dependencies if d["coordinate"] == "fastapi>=0.1"},
                {"service-a"},
            )

    def test_runtime_facts_are_attributed_per_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "a" / "pyproject.toml",
                   '[project]\nname = "a"\nversion = "1"\nrequires-python = ">=3.10"\n')
            _write(root / "b" / "pyproject.toml",
                   '[project]\nname = "b"\nversion = "1"\nrequires-python = ">=3.12"\n')
            report = BuildAnchor(str(root)).inspect()
            # The module is a field, not a suffix a caller has to parse.
            values = {(fact.key, fact.module): fact.value for fact in report.facts}
            self.assertEqual(values.get(("runtime.python", "a")), ">=3.10")
            self.assertEqual(values.get(("runtime.python", "b")), ">=3.12")
            self.assertNotIn("@", "".join(fact.key for fact in report.facts))

    def test_single_project_keys_stay_unqualified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml",
                   '[project]\nname = "solo"\nversion = "1"\nrequires-python = ">=3.11"\n')
            report = BuildAnchor(str(root)).inspect()
            self.assertIn("runtime.python", {fact.key for fact in report.facts})


class LanguageTests(unittest.TestCase):
    def test_a_dockerfile_alone_claims_no_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "Dockerfile", "FROM python:3.11-slim\n")
            report = BuildAnchor(str(root)).inspect()
            for phantom in ("Ruby", "Swift", "C/C++", "Dart", "PHP"):
                self.assertNotIn(phantom, report.languages)

    def test_languages_are_backed_by_files_or_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "app" / "main.py", "print('hi')\n")
            _write(root / "web" / "index.ts", "export const x = 1;\n")
            _write(root / "Makefile", "all:\n\techo hi\n")
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(sorted(report.languages), ["Python", "TypeScript"])
            for entry in report.language_details:
                self.assertTrue(
                    entry["file_count"] > 0 or entry["markers"],
                    f"{entry['language']} was reported with nothing behind it",
                )

    def test_dependency_directories_do_not_contribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "app" / "main.py", "print('hi')\n")
            _write(root / "node_modules" / "vendored" / "thing.rb", "puts 'x'\n")
            _write(root / "node_modules" / "vendored" / "native.c", "int main(){}\n")
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(report.languages, ["Python"])

    def test_marker_language_map_holds_no_ambiguous_markers(self) -> None:
        for ambiguous in ("Dockerfile", "Makefile", "CMakeLists.txt", "BUILD", "WORKSPACE", "global.json"):
            self.assertNotIn(ambiguous, languages.MARKER_LANGUAGES)


class CategoryTests(unittest.TestCase):
    def test_a_shared_library_is_not_a_backend(self) -> None:
        engine = BuildAnchor(".")
        category, _ = engine._categorize_module("lib-a-core", "lib-a", ["sqlalchemy"])
        self.assertEqual(category, "shared")

    def test_an_http_service_is_a_backend(self) -> None:
        engine = BuildAnchor(".")
        category, confidence = engine._categorize_module("service-b", "service-b", ["fastapi"])
        self.assertEqual(category, "backend")
        self.assertEqual(confidence, "high", "name and dependency both pointed the same way")

    def test_nothing_to_go_on_yields_unknown(self) -> None:
        engine = BuildAnchor(".")
        self.assertEqual(engine._categorize_module("zeta", "zeta", []), ("unknown", "none"))

    def test_a_single_signal_is_reported_as_low_confidence(self) -> None:
        """A name alone is a guess, and the report should say which kind it is."""
        engine = BuildAnchor(".")
        category, confidence = engine._categorize_module("api", "api", [])
        self.assertEqual(category, "backend")
        self.assertEqual(confidence, "low")

    def test_file_evidence_can_raise_confidence_without_a_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "index.html", "<!doctype html>")
            category, confidence = BuildAnchor(str(root))._categorize_module(
                "storefront-web", "storefront-web", [], root)
            self.assertEqual(category, "ui")
            self.assertEqual(confidence, "high", "the name and an index.html agreed")

    def test_confidence_reaches_module_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "web-ui" / "package.json", json.dumps({
                "name": "web-ui", "scripts": {"test": "node --test"},
                "dependencies": {"react": "^18.0.0"},
            }))
            module = BuildAnchor(str(root)).inspect().module_details[0]
            self.assertEqual(module["category"], "ui")
            self.assertEqual(module["category_confidence"], "high")


class WalkTests(unittest.TestCase):
    def test_ignored_directories_are_not_descended_into(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "app" / "main.py", "x = 1\n")
            _write(root / "node_modules" / "a" / "b" / "c.js", "1")
            engine = BuildAnchor(str(root))
            # Compare against the engine's resolved workspace: on macOS the
            # temp directory is reached through a /var -> /private/var symlink.
            listed = {str(p.relative_to(engine.workspace)) for p in engine._files()}
            self.assertIn("app/main.py", listed)
            self.assertFalse([p for p in listed if p.startswith("node_modules")])

    def test_symlinked_files_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as inside:
            target = Path(outside) / "secret.py"
            target.write_text("x = 1\n", encoding="utf-8")
            root = Path(inside)
            _write(root / "app.py", "y = 2\n")
            try:
                (root / "link.py").symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            listed = {p.name for p in BuildAnchor(str(root))._files()}
            self.assertIn("app.py", listed)
            self.assertNotIn("link.py", listed)

    def test_digest_still_changes_when_a_manifest_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            before = BuildAnchor(str(root)).inspect().workspace_digest
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "2"\n')
            after = BuildAnchor(str(root)).inspect().workspace_digest
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()


class RepositoryShapeTests(unittest.TestCase):
    """A repository with one project is not a monorepo, and saying so is noise."""

    def test_single_project_at_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "solo"\nversion = "1"\n')
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(report.repository["shape"], "single-project")
            self.assertFalse(report.repository["is_monorepo"])

    def test_root_project_with_a_satellite_is_not_a_monorepo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "core"\nversion = "1"\n')
            _write(root / "sdk" / "node" / "package.json",
                   json.dumps({"name": "sdk", "scripts": {"test": "node --test"}}))
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(report.repository["shape"], "root-plus-satellites")
            self.assertFalse(report.repository["is_monorepo"])

    def test_sibling_projects_with_no_root_are_a_monorepo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "a" / "pyproject.toml", '[project]\nname = "a"\nversion = "1"\n')
            _write(root / "b" / "package.json", json.dumps({"name": "b", "scripts": {"test": "node --test"}}))
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(report.repository["shape"], "monorepo")
            self.assertTrue(report.repository["is_monorepo"])

    def test_a_declared_workspace_is_a_monorepo_even_with_a_root_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "root", "workspaces": ["apps/*"]}))
            _write(root / "apps" / "web" / "package.json",
                   json.dumps({"name": "web", "scripts": {"test": "node --test"}}))
            report = BuildAnchor(str(root)).inspect()
            self.assertEqual(report.repository["shape"], "monorepo")

    def test_scoping_advice_is_withheld_from_a_single_project_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "solo", "scripts": {"test": "node --test"}}))
            block = BuildAnchor(str(root)).llm_prompt().content
            self.assertIn("Single-project repository", block)
            self.assertNotIn("--scope ui", block)

    def test_cmd_and_inspect_agree_about_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "core"\nversion = "1"\n')
            _write(root / "sdk" / "node" / "package.json",
                   json.dumps({"name": "sdk", "scripts": {"test": "node --test"}}))
            engine = BuildAnchor(str(root))
            self.assertEqual(
                engine.resolve_command("test")["is_monorepo"],
                engine.inspect().repository["is_monorepo"],
            )


class ToolAgreementTests(unittest.TestCase):
    """The injected block and the command tool must not contradict each other."""

    def test_injected_block_advertises_the_resolved_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml",
                   '[project]\nname = "x"\nversion = "1"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
            _write(root / "tests" / "test_x.py", "def test_x():\n    assert True\n")
            engine = BuildAnchor(str(root))
            resolved = engine.resolve_command("test")["command"]
            self.assertIn(resolved, engine.llm_prompt().content)

    def test_validation_commands_carry_their_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "sdk" / "node" / "package.json",
                   json.dumps({"name": "sdk", "scripts": {"test": "node --test"}}))
            report = BuildAnchor(str(root)).inspect()
            entry = next(c for c in report.validation_commands if c["command"][0] in ("npm", "pnpm", "yarn", "bun"))
            self.assertEqual(entry["working_directory"], "sdk/node")
            self.assertNotIn("--prefix", entry["command"])


class SchemaVersionTests(unittest.TestCase):
    """A field that changes meaning must change schema version with it."""

    def _polyglot(self, root: Path) -> None:
        _write(root / "service-a" / "pyproject.toml",
               '[project]\nname = "service-a"\nversion = "1"\nrequires-python = ">=3.11"\n')
        _write(root / "lib-a" / "pyproject.toml",
               '[project]\nname = "lib-a"\nversion = "1"\nrequires-python = ">=3.10"\n')

    def test_current_schema_is_declared(self) -> None:
        from buildanchor import schema
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._polyglot(root)
            self.assertEqual(BuildAnchor(str(root)).inspect().schema_version, schema.CURRENT_SCHEMA)

    def test_v1_command_is_runnable_from_the_repository_root(self) -> None:
        """v1's contract was 'run this from the root', so it maps to the shell form."""
        from buildanchor import schema
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._polyglot(root)
            report = BuildAnchor(str(root)).inspect().to_dict()
            rendered = schema.render(report, "v1")
            module = next(m for m in rendered["module_details"] if m["path"] == "service-a")
            self.assertTrue(module["test_command"].startswith("cd service-a && "))

    def test_v1_omits_fields_that_did_not_exist(self) -> None:
        from buildanchor import schema
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._polyglot(root)
            rendered = schema.render(BuildAnchor(str(root)).inspect().to_dict(), "v1")
            self.assertEqual(rendered["schema_version"], "v1")
            self.assertNotIn("language_details", rendered)
            self.assertNotIn("repository", rendered)
            for module in rendered["module_details"]:
                for field in ("working_directory", "test_command_status", "verified_at"):
                    self.assertNotIn(field, module)

    def test_v1_collapses_per_module_facts(self) -> None:
        from buildanchor import schema
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._polyglot(root)
            report = BuildAnchor(str(root)).inspect().to_dict()
            self.assertTrue(any(fact.get("module") for fact in report["facts"]),
                            "v2 should attribute facts to modules")
            rendered = schema.render(report, "v1")
            keys = [fact["key"] for fact in rendered["facts"]]
            self.assertNotIn("@", "".join(keys))
            self.assertEqual(len(keys), len(set(keys)), "v1 cannot express duplicate keys")
            for fact in rendered["facts"]:
                self.assertNotIn("module", fact, "v1 had no per-module facts")

    def test_an_unsupported_schema_is_an_error_not_a_substitution(self) -> None:
        from buildanchor import schema
        from buildanchor.build_truth.core.errors import BuildAnchorError
        with self.assertRaises(BuildAnchorError):
            schema.render({"schema_version": "v2"}, "v3")

    def test_v2_render_is_a_passthrough(self) -> None:
        from buildanchor import schema
        report = {"schema_version": "v2", "module_details": [], "facts": []}
        self.assertIs(schema.render(report, "v2"), report)


class VersionSyncTests(unittest.TestCase):
    def test_every_artifact_declares_the_same_version(self) -> None:
        """A Homebrew user reading the changelog must see one product."""
        import subprocess
        import sys as _sys
        repo = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [_sys.executable, str(repo / "scripts" / "bump_version.py"), "--check"],
            capture_output=True, text=True, check=False, cwd=repo,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FileEnumerationTests(unittest.TestCase):
    """What the repository already ignores should cost nothing."""

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        import subprocess
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)

    def _repo(self, root: Path) -> None:
        self._git(root, "init")
        self._git(root, "config", "user.email", "t@example.com")
        self._git(root, "config", "user.name", "Test")
        _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
        _write(root / "app.py", "x = 1\n")

    def test_gitignored_files_are_not_considered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root)
            _write(root / ".gitignore", "generated/\n")
            _write(root / "generated" / "big.py", "y = 2\n")
            engine = BuildAnchor(str(root))
            listed = {p.name for p in engine._files()}
            self.assertIn("app.py", listed)
            self.assertNotIn("big.py", listed, "a gitignored file was analysed")

    def test_untracked_but_unignored_files_are_still_seen(self) -> None:
        """A package.json created a moment ago must not be invisible."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root)
            _write(root / "web" / "package.json",
                   json.dumps({"name": "web", "scripts": {"test": "node --test"}}))
            modules = {m.path for m in BuildAnchor(str(root)).discover_modules()}
            self.assertIn("web", modules)

    def test_both_enumeration_paths_agree_outside_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root)
            _write(root / "web" / "package.json", json.dumps({"name": "web"}))
            engine = BuildAnchor(str(root))
            from_git = engine._git_tracked_files()
            self.assertIsNotNone(from_git, "git enumeration unavailable in this environment")
            engine._invalidate_scan()
            with unittest.mock.patch.object(type(engine), "_git_tracked_files", lambda self: None):
                from_walk = engine._files()
            self.assertEqual(
                sorted(str(p.relative_to(engine.workspace)) for p in from_git),
                sorted(str(p.relative_to(engine.workspace)) for p in from_walk),
            )

    def test_a_non_git_directory_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            self.assertIn("pyproject.toml", {p.name for p in BuildAnchor(str(root))._files()})


class RevalidationWindowTests(unittest.TestCase):
    def test_repeated_calls_inside_the_window_do_not_rewalk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            engine = BuildAnchor(str(root))
            engine.inspect()
            calls = {"count": 0}
            original = type(engine)._files

            def counted(self, *args, **kwargs):
                calls["count"] += 1
                return original(self, *args, **kwargs)

            with unittest.mock.patch.object(type(engine), "_files", counted):
                for _ in range(5):
                    engine._inspect_cached()
            self.assertEqual(calls["count"], 0, "revalidated inside the window")

    def test_the_window_can_be_disabled(self) -> None:
        import os as _os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            engine = BuildAnchor(str(root))
            engine.inspect()
            with unittest.mock.patch.dict(_os.environ, {"BUILDANCHOR_REVALIDATE_MS": "0"}):
                calls = {"count": 0}
                original = type(engine)._files

                def counted(self, *args, **kwargs):
                    calls["count"] += 1
                    return original(self, *args, **kwargs)

                with unittest.mock.patch.object(type(engine), "_files", counted):
                    engine._inspect_cached()
                self.assertEqual(calls["count"], 1, "the window was not disabled")

    def test_refresh_bypasses_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            engine = BuildAnchor(str(root))
            first = engine.inspect().workspace_digest
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "2"\n')
            engine._invalidate_scan()
            self.assertNotEqual(engine.inspect().workspace_digest, first)


class ImportIndexTests(unittest.TestCase):
    def test_usage_lookup_reuses_one_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            _write(root / "app.py", "import requests\n\nrequests.get('x')\n")
            engine = BuildAnchor(str(root))
            first = engine._import_index()
            self.assertIs(engine._import_index(), first, "the index was rebuilt")
            hits = engine._grep_usage("requests", {".py"})
            self.assertEqual(hits[0]["file"], "app.py")
            self.assertEqual(hits[0]["line"], 1)

    def test_the_index_excludes_dependency_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            _write(root / "node_modules" / "pkg" / "index.js", "import lodash from 'lodash';\n")
            engine = BuildAnchor(str(root))
            self.assertEqual(engine._grep_usage("lodash", {".js"}), [])


class DoctorTests(unittest.TestCase):
    """"Why isn't my project showing up?" must have a real answer."""

    def test_a_discovered_module_is_explained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "web" / "package.json",
                   json.dumps({"name": "web", "scripts": {"test": "node --test"}}))
            result = BuildAnchor(str(root)).diagnose("web")
            self.assertTrue(result["is_module"])
            self.assertEqual(result["status"], "valid")
            self.assertIn("node", result["reason"])

    def test_a_package_without_scripts_says_so_and_says_what_to_do(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "types" / "package.json", json.dumps({"name": "types"}))
            result = BuildAnchor(str(root)).diagnose("types")
            self.assertFalse(result["is_module"])
            self.assertIn("no 'test' or 'build' script", result["reason"])
            self.assertTrue(any("script" in s for s in result["suggestions"]))

    def test_a_directory_with_no_marker_lists_the_markers_it_would_need(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "docs" / "notes.md", "hello\n")
            result = BuildAnchor(str(root)).diagnose("docs")
            self.assertIn("no project marker", result["reason"])
            self.assertTrue(any("pyproject.toml" in s for s in result["suggestions"]))

    def test_a_too_deep_python_project_is_explained_by_the_actual_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "a" / "b" / "c" / "pyproject.toml", '[project]\nname = "c"\nversion = "1"\n')
            result = BuildAnchor(str(root)).diagnose("a/b/c")
            self.assertIn("directories deep", result["reason"])

    def test_malformed_json_is_reported_as_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "broken" / "package.json", "{not json")
            result = BuildAnchor(str(root)).diagnose("broken")
            self.assertIn("not valid JSON", result["reason"])

    def test_a_missing_directory_is_an_error_not_a_shrug(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = BuildAnchor(directory).diagnose("nope")
            self.assertEqual(result["status"], "invalid")
            self.assertFalse(result["exists"])

    def test_a_path_outside_the_workspace_is_refused(self) -> None:
        from buildanchor.build_truth.core.errors import BuildAnchorError
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BuildAnchorError):
                BuildAnchor(directory).diagnose("../..")

    def test_repository_diagnosis_reports_a_broken_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "svc" / "pyproject.toml", '[project]\nname = "svc"\nversion = "1"\n')
            engine = BuildAnchor(str(root))
            engine.verify_commands(level="collects")
            result = BuildAnchor(str(root)).diagnose()
            severities = {finding["severity"] for finding in result["findings"]}
            self.assertTrue(severities, "a repository with an unproven module reported nothing")

    def test_diagnosis_never_contradicts_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "web" / "package.json",
                   json.dumps({"name": "web", "scripts": {"test": "node --test"}}))
            engine = BuildAnchor(str(root))
            diagnosed = {m["path"] for m in engine.diagnose()["modules"]}
            reported = {m["path"] for m in engine.inspect().module_details}
            self.assertEqual(diagnosed, reported)


class CompatibilityRuleReviewTests(unittest.TestCase):
    """Rules encode facts about the world, and the world moves."""

    def test_every_rule_carries_a_review_date(self) -> None:
        from buildanchor import compatibility
        for rule in compatibility.all_rules():
            self.assertIn("reviewed", rule, f"{rule.get('code')} has no review date")
            from datetime import date
            date.fromisoformat(str(rule["reviewed"]))  # raises if malformed

    def test_no_rule_is_past_its_review_horizon(self) -> None:
        """When this fails, re-confirm the rules and update their dates.

        Do not simply move the horizon: the failure exists so that advice about
        Spring Boot namespaces or Rust editions gets re-checked by a person
        rather than inherited indefinitely.
        """
        from buildanchor import compatibility
        stale = compatibility.stale_rules()
        self.assertEqual(
            [], [rule["code"] for rule in stale],
            "these rules are older than the review horizon and must be re-confirmed",
        )

    def test_the_horizon_actually_fires(self) -> None:
        from datetime import datetime, timedelta, timezone

        from buildanchor import compatibility
        far_future = (datetime.now(timezone.utc).date()
                      + timedelta(days=compatibility.RULE_REVIEW_HORIZON_DAYS + 400))
        self.assertTrue(compatibility.stale_rules(today=far_future),
                        "the review horizon never triggers, so it protects nothing")

    def test_stale_rules_are_reported_as_a_limitation(self) -> None:
        import unittest.mock as _mock

        from buildanchor.build_truth.features import inspection
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", '[project]\nname = "x"\nversion = "1"\n')
            # Patch where the name is bound, not where it is defined.
            with _mock.patch.object(inspection, "compatibility_stale_rules",
                                    lambda *a, **k: [{"code": "SOME_RULE"}]):
                report = BuildAnchor(str(root)).inspect()
            self.assertTrue(any("review horizon" in note for note in report.limitations),
                            report.limitations)


class ManifestParserEquivalenceTests(unittest.TestCase):
    """Two parsers exist only because BuildAnchor has no runtime dependencies.

    ``tomllib`` arrived in Python 3.11 and this project supports 3.10, so the
    choice is between a conditional dependency and a fallback. Zero runtime
    dependencies is a defended property — it is what makes the tool auditable
    and offline — so the fallback stays, and these tests make divergence
    impossible to introduce quietly. Two parsers that are proven to agree are a
    different thing from two parsers that merely have not been caught yet.
    """

    CORPUS = (
        '[project]\nname = "a"\nversion = "1"\ndependencies = ["fastapi>=0.100.0", "sqlalchemy>=2.0"]\n',
        '[project]\nname = "b"\nversion = "1"\ndependencies = [\n  "httpx>=0.25.0",\n  "click",\n]\n',
        '[project]\nname = "c"\nversion = "1"\n\n[project.optional-dependencies]\n'
        'dev = ["pytest>=7", "coverage>=7"]\ndocs = ["sphinx>=7"]\n',
        '[project]\nname = "d"\nversion = "1"\ndependencies = []\n',
        '[project]\nname = "e"\nversion = "1"\nrequires-python = ">=3.11"\n'
        'dependencies = ["a==1.0", "b~=2.0", "c!=3.0"]\n',
        '[project]\nname = "f"\nversion = "1"\n'
        'dependencies = ["uvicorn[standard]>=0.30", "pkg; python_version < \'3.11\'"]\n',
        '[build-system]\nrequires = ["setuptools>=68"]\n\n[project]\nname = "g"\nversion = "1"\n'
        'dependencies = ["one>=1"]\n\n[project.optional-dependencies]\ndev = ["two>=2"]\n',
    )

    def test_both_parsers_agree_on_every_sample(self) -> None:
        from buildanchor.build_truth.core import manifest_parsing
        if manifest_parsing._tomllib is None:
            self.skipTest("no tomllib on this interpreter; only one path exists here")
        for sample in self.CORPUS:
            with self.subTest(sample=sample.splitlines()[1]):
                self.assertEqual(
                    manifest_parsing.python_dependencies(sample),
                    manifest_parsing._dependencies_from_text(sample),
                    "the tomllib path and the fallback disagree",
                )

    def test_both_paths_agree_on_requires_python(self) -> None:
        import unittest.mock as _mock

        from buildanchor.build_truth.core import manifest_parsing
        for sample in self.CORPUS:
            expected = manifest_parsing.requires_python(sample)
            with _mock.patch.object(manifest_parsing, "_tomllib", None):
                self.assertEqual(manifest_parsing.requires_python(sample), expected)

    def test_the_fallback_is_actually_exercised_somewhere(self) -> None:
        """CI runs 3.10, where the fallback is the only path. Assert it works."""
        import unittest.mock as _mock

        from buildanchor.build_truth.core import manifest_parsing
        sample = self.CORPUS[2]
        with _mock.patch.object(manifest_parsing, "_tomllib", None):
            parsed = manifest_parsing.python_dependencies(sample)
        self.assertEqual([c for c, _ in parsed], ["pytest>=7", "coverage>=7", "sphinx>=7"])


class SilentFailureTests(unittest.TestCase):
    """A confident empty answer is the worst failure mode available."""

    def test_a_malformed_package_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", "{oops")
            report = BuildAnchor(str(root)).inspect()
            self.assertNotEqual(report.status, "valid",
                                "an unreadable manifest was reported as a clean bill of health")
            self.assertTrue(any("could not be parsed" in note for note in report.limitations),
                            report.limitations)

    def test_a_malformed_pyproject_is_reported(self) -> None:
        from buildanchor.build_truth.core import manifest_parsing
        if manifest_parsing._tomllib is None:
            self.skipTest("cannot detect malformed TOML without tomllib")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "pyproject.toml", "[project\nname = broken")
            report = BuildAnchor(str(root)).inspect()
            self.assertTrue(any("could not be parsed" in note for note in report.limitations))

    def test_an_empty_manifest_is_not_called_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", "")
            report = BuildAnchor(str(root)).inspect()
            self.assertFalse(any("could not be parsed" in note for note in report.limitations))

    def test_a_healthy_repository_is_still_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "x", "scripts": {"test": "node --test"}}))
            self.assertEqual(BuildAnchor(str(root)).inspect().status, "valid")

    def test_doctor_reports_a_broken_manifest_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", "{oops")
            result = BuildAnchor(str(root)).diagnose()
            self.assertEqual(result["status"], "invalid")
            self.assertTrue(any(f["severity"] == "error" for f in result["findings"]))


class ExitCodeTests(unittest.TestCase):
    """The exit code must not disagree with the payload it accompanies."""

    def _run(self, *args: str) -> int:
        import contextlib
        import io

        from buildanchor.cli import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args))

    def test_inspect_on_an_empty_directory_is_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self._run("inspect", "--workspace", directory, "--format", "json"), 2)

    def test_inspect_on_a_healthy_repository_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "x", "scripts": {"test": "node --test"}}))
            self.assertEqual(self._run("inspect", "--workspace", str(root), "--format", "json"), 0)

    def test_a_refused_request_exits_four(self) -> None:
        self.assertEqual(self._run("inspect", "--workspace", "/no/such/place", "--format", "json"), 4)

    def test_an_unsupported_schema_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                self._run("inspect", "--workspace", directory, "--format", "json", "--schema", "v9"), 4)


class DeclaredConventionTests(unittest.TestCase):
    """A repository that says how it builds should be believed."""

    def _repo(self, root: Path, files: dict[str, str]) -> None:
        for name, content in files.items():
            _write(root / name, content)

    def test_a_justfile_beats_the_ecosystem_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "justfile": "test:\n    cargo nextest run\n\nbuild:\n    cargo build --release\n",
                "Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n',
            })
            resolved = BuildAnchor(str(root)).resolve_command("test")
            self.assertEqual(resolved["command"], "just test")
            self.assertIn("justfile", resolved["source"])

    def test_a_taskfile_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "Taskfile.yml": 'version: "3"\n\ntasks:\n  test:\n    cmds:\n      - go test ./...\n',
                "go.mod": "module x\n\ngo 1.22\n",
            })
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "task test")

    def test_mise_tasks_are_respected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "mise.toml": '[tasks.test]\nrun = "pytest -x"\n',
                "pyproject.toml": '[project]\nname = "x"\nversion = "1"\n',
            })
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "mise run test")

    def test_nox_sessions_are_respected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "noxfile.py": 'import nox\n\n@nox.session\ndef tests(session):\n    session.run("pytest")\n',
                "pyproject.toml": '[project]\nname = "x"\nversion = "1"\n',
            })
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "nox -s tests")

    def test_tox_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "tox.ini": "[tox]\nenvlist = py310\n",
                "pyproject.toml": '[project]\nname = "x"\nversion = "1"\n',
            })
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "tox")

    def test_a_makefile_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {"Makefile": ".PHONY: test\ntest:\n\tpytest tests/\n"})
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "make test")

    def test_a_runner_without_the_phase_falls_through(self) -> None:
        """A justfile with no test recipe must not block the real answer."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._repo(root, {
                "justfile": "deploy:\n    ./deploy.sh\n",
                "package.json": json.dumps({"name": "x", "scripts": {"test": "node --test"}}),
            })
            self.assertEqual(BuildAnchor(str(root)).resolve_command("test")["command"], "npm test")

    def test_phony_and_variables_are_not_targets(self) -> None:
        from buildanchor.build_truth.core import conventions
        targets = conventions._make_targets("CC := gcc\n.PHONY: test\ntest:\n\techo hi\n")
        self.assertIn("test", targets)
        self.assertNotIn("CC", targets)


class ReversibilityTests(unittest.TestCase):
    """A tool that edits the file agents read must be trivially reversible."""

    def _project(self, root: Path) -> None:
        _write(root / "package.json", json.dumps({"name": "x", "scripts": {"test": "node --test"}}))
        _write(root / "CLAUDE.md", "# House rules\n\nBe careful with migrations.\n")

    def _run(self, *args: str) -> int:
        import contextlib
        import io

        from buildanchor.cli import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args))

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            before = (root / "CLAUDE.md").read_bytes()
            self.assertEqual(self._run("init", "--workspace", str(root), "--dry-run"), 0)
            self.assertEqual((root / "CLAUDE.md").read_bytes(), before)
            self.assertFalse((root / ".buildanchor.json").exists())

    def test_undo_restores_the_file_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            before = (root / "CLAUDE.md").read_bytes()
            self._run("init", "--workspace", str(root))
            self.assertIn(b"BuildAnchor", (root / "CLAUDE.md").read_bytes())
            self._run("init", "--workspace", str(root), "--undo")
            self.assertEqual((root / "CLAUDE.md").read_bytes(), before)
            self.assertFalse((root / ".buildanchor.json").exists())

    def test_undo_removes_a_file_it_created_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "x", "scripts": {"test": "node --test"}}))
            self._run("init", "--workspace", str(root))
            self.assertTrue((root / "AGENTS.md").is_file())
            self._run("init", "--workspace", str(root), "--undo")
            self.assertFalse((root / "AGENTS.md").exists(), "left an empty file behind")

    def test_undo_on_a_clean_repository_is_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            before = (root / "CLAUDE.md").read_bytes()
            self.assertEqual(self._run("init", "--workspace", str(root), "--undo"), 0)
            self.assertEqual((root / "CLAUDE.md").read_bytes(), before)

    def test_verify_dry_run_executes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "package.json", json.dumps({"name": "x", "scripts": {"test": "node --test"}}))
            result = BuildAnchor(str(root)).verify_commands(level="passes", dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["results"], [])
            self.assertFalse((root / ".buildanchor" / "verified.json").exists())
            commands = [step["command"] for entry in result["plan"] for step in entry["would_run"]]
            self.assertTrue(any(c == "npm test" for c in commands))

    def test_a_runner_is_not_listed_twice_on_a_case_insensitive_filesystem(self) -> None:
        from buildanchor.build_truth.core import conventions
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "justfile", "test:\n    echo hi\n")
            runners = conventions.declared_runners(root)
            self.assertEqual(len(runners), len({name.lower() for name in runners}))


class PathSeparatorTests(unittest.TestCase):
    """Repository-relative paths are data, so they must not vary by platform.

    On Windows ``str(Path("sdk") / "node")`` is ``sdk\\node``. These strings end
    up in reports, in evidence entries, and in the *committed* verification
    cache's keys — so a Windows contributor and a Linux one would produce two
    different reports for the same repository, and a cache that churns on every
    platform switch. Git speaks forward slashes; so does this.
    """

    def _monorepo(self, root: Path) -> None:
        _write(root / "sdk" / "node" / "package.json",
               json.dumps({"name": "sdk", "scripts": {"test": "node --test"}}))
        _write(root / "services" / "api" / "pyproject.toml",
               '[project]\nname = "api"\nversion = "1"\n')

    def test_module_paths_use_forward_slashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root)
            for module in BuildAnchor(str(root)).inspect().module_details:
                self.assertNotIn("\\", module["path"])
                self.assertNotIn("\\", module["working_directory"])
            paths = {m["path"] for m in BuildAnchor(str(root)).inspect().module_details}
            self.assertIn("sdk/node", paths)

    def test_evidence_paths_use_forward_slashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root)
            for item in BuildAnchor(str(root)).inspect().evidence:
                self.assertNotIn("\\", item.path)

    def test_the_committed_cache_uses_forward_slashes(self) -> None:
        """Otherwise the file churns whenever the platform changes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root)
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            written = (root / ".buildanchor" / "verified.json").read_text(encoding="utf-8")
            self.assertNotIn("\\\\", written)
            self.assertIn("sdk/node::test", json.loads(written)["entries"])

    def test_shell_form_matches_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root)
            for module in BuildAnchor(str(root)).inspect().module_details:
                if module["working_directory"] != "." and module["test_command_shell"]:
                    self.assertIn(f"cd {module['working_directory']} &&",
                                  module["test_command_shell"])

    def test_a_path_outside_the_workspace_is_still_refused(self) -> None:
        """`_relative` does not raise, so containment is checked explicitly."""
        from buildanchor.build_truth.core.errors import BuildAnchorError
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BuildAnchorError):
                BuildAnchor(directory).diagnose("../..")


class DriftCheckTests(unittest.TestCase):
    """`init --check` must fire on drift and stay quiet on proof state.

    The guidance block carries how far each command is proven, and that moves
    constantly: somebody runs `verify`, a manifest changes, a fresh clone has no
    cache. None of those mean the guidance stopped describing the repository. A
    gate that fails for them is a gate people learn to ignore — and this one did
    exactly that in CI before it was fixed.
    """

    def _run(self, *args: str) -> int:
        import contextlib
        import io

        from buildanchor.cli import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args))

    def _project(self, root: Path) -> None:
        _write(root / "package.json",
               json.dumps({"name": "app", "scripts": {"test": "node --test"}}))
        _write(root / "index.test.js", "const t=require('node:test');t('x',()=>{});\n")

    def test_running_verify_does_not_make_the_guidance_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            self._run("init", "--workspace", str(root))
            self.assertEqual(self._run("init", "--workspace", str(root), "--check"), 0)

            BuildAnchor(str(root)).verify_commands(level="resolvable")
            self.assertEqual(
                self._run("init", "--workspace", str(root), "--check"), 0,
                "running verify made the guidance look stale",
            )

    def test_a_missing_cache_does_not_make_the_guidance_stale(self) -> None:
        """A fresh clone has no verification record; that is not drift."""
        import shutil as _shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            self._run("init", "--workspace", str(root))
            _shutil.rmtree(root / ".buildanchor")
            self.assertEqual(
                self._run("init", "--workspace", str(root), "--check"), 0,
                "a checkout without a cache looked stale",
            )

    def test_a_changed_command_is_still_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            self._run("init", "--workspace", str(root))
            guidance = root / "AGENTS.md"
            guidance.write_text(
                guidance.read_text(encoding="utf-8").replace("npm test", "yarn test"),
                encoding="utf-8")
            self.assertEqual(self._run("init", "--workspace", str(root), "--check"), 1)

    def test_a_new_module_is_still_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            self._run("init", "--workspace", str(root))
            _write(root / "packages" / "api" / "package.json",
                   json.dumps({"name": "api", "scripts": {"test": "node --test"}}))
            self.assertEqual(self._run("init", "--workspace", str(root), "--check"), 1)

    def test_the_written_block_still_shows_the_proof_status(self) -> None:
        """Ignored for comparison, but a reader should still see it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            self._run("init", "--workspace", str(root))
            written = (root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertTrue("resolvable" in written or "declared" in written)
