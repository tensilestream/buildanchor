# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for working directories and the command verification ladder."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from buildanchor import BuildAnchor
from buildanchor.build_truth.core import toolchain
from buildanchor.build_truth.core.verification_levels import LEVELS, at_least, rank


def _venv_layout(directory: Path, with_pytest: bool = True) -> None:
    """Create the parts of a virtualenv that static resolution looks at."""
    binaries = directory / ".venv" / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    (binaries / "python").write_text("", encoding="utf-8")
    if with_pytest:
        (binaries / "pytest").write_text("", encoding="utf-8")


def _real_venv_layout(directory: Path) -> bool:
    """Link the running virtualenv in as the project's own, so probes execute.

    Copying or re-linking only the interpreter would produce a venv with no
    site-packages; the whole prefix has to come across for pytest to import.
    Returns False when the tests are not running inside a usable venv.
    """
    if sys.prefix == sys.base_prefix:
        return False
    for name in ("bin/pytest", "Scripts/pytest.exe"):
        if (Path(sys.prefix) / name).exists():
            break
    else:
        return False
    try:
        (directory / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    except OSError:
        return False
    return True


class LadderTests(unittest.TestCase):
    def test_rungs_are_ordered_and_comparable(self) -> None:
        self.assertEqual(LEVELS, ("declared", "resolvable", "collects", "passes"))
        self.assertTrue(at_least("collects", "resolvable"))
        self.assertFalse(at_least("declared", "collects"))
        self.assertEqual(rank("skipped"), -1, "outcomes are not rungs")

    def test_unknown_level_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = BuildAnchor(directory)
            with self.assertRaises(Exception):
                engine.verify_commands(level="probably-fine")


class WorkingDirectoryTests(unittest.TestCase):
    """Every module command must state the directory it runs in."""

    def _polyglot_root(self, directory: str) -> Path:
        root = Path(directory)
        for name in ("service-a", "lib-a"):
            module = root / name
            module.mkdir()
            (module / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8",
            )
        web = root / "web-ui"
        web.mkdir()
        (web / "package.json").write_text(
            json.dumps({"name": "web-ui", "scripts": {"test": "vitest run", "build": "vite build"}}),
            encoding="utf-8",
        )
        return root

    def test_every_module_carries_a_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._polyglot_root(directory)
            modules = BuildAnchor(str(root)).discover_modules()
            self.assertTrue(modules)
            for module in modules:
                self.assertTrue(module.working_directory, f"{module.name} has no working directory")

    def test_python_command_is_relative_to_its_module(self) -> None:
        """`python -m pytest <path>` from the root cannot import the package."""
        with tempfile.TemporaryDirectory() as directory:
            root = self._polyglot_root(directory)
            modules = {m.path: m for m in BuildAnchor(str(root)).discover_modules()}
            service = modules["service-a"]
            self.assertEqual(service.working_directory, "service-a")
            self.assertNotIn("service-a", service.test_command)
            self.assertEqual(service.test_command_shell, f"cd service-a && {service.test_command}")

    def test_declared_environment_is_preferred_over_the_ambient_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._polyglot_root(directory)
            _venv_layout(root / "service-a")
            modules = {m.path: m for m in BuildAnchor(str(root)).discover_modules()}
            self.assertIn(".venv", modules["service-a"].test_command)
            self.assertNotIn(".venv", modules["lib-a"].test_command)

    def test_reactor_style_modules_run_from_the_root(self) -> None:
        """A `-pl` invocation is driven from the root; saying otherwise is wrong."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text(
                "<project><modules><module>svc</module></modules></project>", encoding="utf-8",
            )
            (root / "svc").mkdir()
            (root / "svc" / "pom.xml").write_text(
                "<project><artifactId>svc</artifactId></project>", encoding="utf-8",
            )
            modules = {m.path: m for m in BuildAnchor(str(root)).discover_modules()}
            self.assertEqual(modules["svc"].working_directory, ".")
            self.assertEqual(modules["svc"].test_command_shell, modules["svc"].test_command)


class VerificationTests(unittest.TestCase):
    def _python_project(self, root: Path, passing: bool = True) -> None:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        body = "def test_demo():\n    assert True\n" if passing else "import definitely_not_installed\n"
        (tests / "test_demo.py").write_text(body, encoding="utf-8")

    def test_resolvable_rung_executes_nothing_and_catches_a_missing_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._python_project(root)
            _venv_layout(root, with_pytest=False)
            result = BuildAnchor(str(root)).verify_commands(level="resolvable", write_cache=False)
            entry = result["results"][0]
            self.assertEqual(entry["outcome"], "failed")
            self.assertIn("pytest", entry["reason"])
            self.assertEqual([r["level"] for r in entry["rungs"]], ["resolvable"])

    def test_collects_rung_catches_a_suite_that_cannot_import(self) -> None:
        """The failure the ladder exists for: declared, resolvable, still broken.

        This is the shape reported from a real polyglot monorepo — a command
        that looks fine statically and dies during collection.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._python_project(root, passing=False)
            if not _real_venv_layout(root):
                self.skipTest("no pytest-bearing interpreter to probe with")
            result = BuildAnchor(str(root)).verify_commands(level="collects", write_cache=False)
            entry = next(iter(result["results"]))
            self.assertTrue(entry["rungs"][0]["passed"], "the runner itself resolves")
            self.assertEqual(entry["level_reached"], "resolvable")
            self.assertEqual(entry["outcome"], "failed")
            collects = next(r for r in entry["rungs"] if r["level"] == "collects")
            self.assertIn("definitely_not_installed", collects["output_tail"])
            self.assertEqual(result["modules_at_collects_or_better"], 0)

    def test_a_healthy_suite_reaches_collects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._python_project(root, passing=True)
            if not _real_venv_layout(root):
                self.skipTest("no pytest-bearing interpreter to probe with")
            result = BuildAnchor(str(root)).verify_commands(level="collects", write_cache=False)
            entry = result["results"][0]
            self.assertEqual(entry["level_reached"], "collects")
            self.assertEqual(result["status"], "valid")

    def test_a_module_with_no_probe_is_skipped_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                json.dumps({"name": "app", "scripts": {"test": "node run-my-tests.js"}}), encoding="utf-8",
            )
            result = BuildAnchor(str(root)).verify_commands(level="collects", write_cache=False)
            entry = result["results"][0]
            if entry["rungs"] and entry["rungs"][0]["passed"] is False:
                self.skipTest("npm is unavailable in this environment")
            collects = [r for r in entry["rungs"] if r["level"] == "collects"]
            self.assertTrue(collects)
            self.assertIsNone(collects[0]["passed"], "an unknown runner must not be guessed either way")
            self.assertIn("no discovery-only mode", collects[0]["detail"])


class CacheTests(unittest.TestCase):
    def _verified_project(self, root: Path) -> None:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )

    def test_result_is_recorded_and_replayed_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._verified_project(root)
            engine = BuildAnchor(str(root))
            first = engine.verify_commands(level="resolvable")
            self.assertTrue((root / ".buildanchor" / "verified.json").is_file())

            replay = BuildAnchor(str(root)).verify_commands(level="resolvable")
            self.assertTrue(replay["results"][0]["cached"])
            self.assertEqual(replay["results"][0]["level_reached"], first["results"][0]["level_reached"])

    def test_a_changed_manifest_invalidates_the_recorded_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._verified_project(root)
            BuildAnchor(str(root)).verify_commands(level="resolvable")

            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.2.0"\ndependencies = ["httpx"]\n', encoding="utf-8",
            )
            status = BuildAnchor(str(root)).resolve_command("test").get("command_status")
            self.assertEqual(status, "declared", "a stale result must not be reported as proven")

    def test_the_cache_does_not_disturb_the_workspace_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._verified_project(root)
            before = BuildAnchor(str(root)).inspect().workspace_digest
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            after = BuildAnchor(str(root)).inspect().workspace_digest
            self.assertEqual(before, after)

    def test_status_reaches_module_details_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "service-a"
            module.mkdir()
            (module / "pyproject.toml").write_text(
                '[project]\nname = "service-a"\nversion = "0.1.0"\n', encoding="utf-8",
            )
            _venv_layout(module)
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            details = {m["path"]: m for m in BuildAnchor(str(root)).inspect().module_details}
            self.assertEqual(details["service-a"]["test_command_status"], "resolvable")
            self.assertIsNotNone(details["service-a"]["verified_at"])


class ToolchainTests(unittest.TestCase):
    def test_lockfiles_select_the_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            runner, source = toolchain.node_runner(root)
            self.assertEqual((runner, source), ("pnpm", "pnpm-lock.yaml"))

    def test_workspace_lockfile_is_inherited_by_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "yarn.lock").write_text("", encoding="utf-8")
            member = root / "apps" / "web"
            member.mkdir(parents=True)
            self.assertEqual(toolchain.node_runner(member, root)[0], "yarn")

    def test_wrapper_is_preferred_and_made_relative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mvnw").write_text("", encoding="utf-8")
            argv = toolchain.wrapper_aware("maven", ["mvn", "test"], root, root)
            self.assertEqual(argv[0], "./mvnw")

    def test_unknown_node_runner_yields_no_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            probe, reason = toolchain.node_collect_probe(Path(directory), "node ./run-tests.js")
            self.assertIsNone(probe)
            self.assertIn("no discovery-only mode", reason)


if __name__ == "__main__":
    unittest.main()


class RedundantWorkTests(unittest.TestCase):
    """Validating the cache costs a full walk, so it must happen once per request."""

    def _counted_engine(self, root):
        from buildanchor.engine import BuildAnchor as Engine
        walks = {"count": 0}
        original = Engine._files

        def counted(self, *args, **kwargs):
            walks["count"] += 1
            return original(self, *args, **kwargs)

        Engine._files = counted
        self.addCleanup(setattr, Engine, "_files", original)
        return walks

    def test_a_warm_resolve_command_walks_the_tree_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
            engine = BuildAnchor(str(root))
            engine.resolve_command("test")
            walks = self._counted_engine(root)
            engine.resolve_command("test")
            self.assertLessEqual(walks["count"], 1, "the repository was walked more than once")

    def test_a_warm_mcp_tool_call_walks_the_tree_once(self) -> None:
        from buildanchor.transports import MCPServer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
            server = MCPServer(str(root))
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_test_command", "arguments": {}}}
            server.handle(request)
            walks = self._counted_engine(root)
            server.handle(request)
            self.assertLessEqual(walks["count"], 1, "the repository was walked more than once")

    def test_editing_a_source_file_does_not_invalidate_the_report(self) -> None:
        """Nothing in the report derives from a source file's contents."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\n', encoding="utf-8")
            source = root / "app.py"
            source.write_text("x = 1\n", encoding="utf-8")
            before = BuildAnchor(str(root)).inspect().workspace_digest
            source.write_text("x = 2  # a different body, same shape\n", encoding="utf-8")
            self.assertEqual(BuildAnchor(str(root)).inspect().workspace_digest, before)

    def test_adding_a_file_does_invalidate_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\n', encoding="utf-8")
            before = BuildAnchor(str(root)).inspect().workspace_digest
            (root / "extra.py").write_text("y = 1\n", encoding="utf-8")
            self.assertNotEqual(BuildAnchor(str(root)).inspect().workspace_digest, before)


class ProbeCoverageTests(unittest.TestCase):
    """A rung nobody can reach is not a ladder."""

    def test_node_builtin_runner_has_a_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            probe, reason = toolchain.node_collect_probe(Path(directory), "node --test")
            self.assertIsNotNone(probe)
            self.assertIn("--test-name-pattern", probe)
            self.assertIn(toolchain.NEVER_MATCHES, probe)
            self.assertIn("no test body run", reason)

    def test_unittest_probe_is_derived_from_the_command(self) -> None:
        probe, reason = toolchain.python_collect_probe_for(
            ["python", "-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(probe[-2:], ["-k", toolchain.NEVER_MATCHES])
        self.assertIn("no test body run", reason)

    def test_pytest_probe_is_still_collect_only(self) -> None:
        probe, _ = toolchain.python_collect_probe_for(["uv", "run", "pytest"])
        self.assertEqual(probe, ["uv", "run", "pytest", "--collect-only", "-q"])

    def test_an_unknown_python_runner_is_skipped_not_guessed(self) -> None:
        probe, reason = toolchain.python_collect_probe_for(["python", "run_my_tests.py"])
        self.assertIsNone(probe)
        self.assertIn("no discovery-only mode", reason)

    def test_dotnet_has_a_probe(self) -> None:
        self.assertIn("dotnet", toolchain.COMPILED_COLLECT_PROBES)
        self.assertIn("--list-tests", toolchain.COMPILED_COLLECT_PROBES["dotnet"])

    def test_never_matches_pattern_matches_nothing(self) -> None:
        import re
        for candidate in ("", "test", "a b c", "$^"):
            self.assertIsNone(re.search(toolchain.NEVER_MATCHES, candidate))


class ParallelVerificationTests(unittest.TestCase):
    def _monorepo(self, root: Path, count: int = 4) -> None:
        for index in range(count):
            module = root / f"mod-{index}"
            (module / "tests").mkdir(parents=True)
            (module / "pyproject.toml").write_text(
                f'[project]\nname = "mod-{index}"\nversion = "1"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
            (module / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    def test_results_keep_module_order_regardless_of_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root)
            engine = BuildAnchor(str(root))
            parallel = engine.verify_commands(level="resolvable", jobs=4, write_cache=False)
            serial = BuildAnchor(str(root)).verify_commands(level="resolvable", jobs=1, write_cache=False)
            self.assertEqual([r["path"] for r in parallel["results"]],
                             [r["path"] for r in serial["results"]])
            self.assertEqual([r["level_reached"] for r in parallel["results"]],
                             [r["level_reached"] for r in serial["results"]])

    def test_worker_count_never_exceeds_the_module_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._monorepo(root, count=2)
            result = BuildAnchor(str(root)).verify_commands(level="resolvable", jobs=16, write_cache=False)
            self.assertLessEqual(result["workers"], 2)


class CommittableCacheTests(unittest.TestCase):
    """The cache is a fact about the repository, so it must survive review."""

    def _project(self, root: Path) -> None:
        (root / "tests").mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
        (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    def test_rerunning_with_no_change_produces_no_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            cache = root / ".buildanchor" / "verified.json"
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            first = cache.read_bytes()
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            self.assertEqual(cache.read_bytes(), first, "a no-op run churned the committed file")

    def test_the_cache_holds_nothing_machine_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            written = (root / ".buildanchor" / "verified.json").read_text(encoding="utf-8")
            self.assertNotIn(str(root), written, "an absolute path leaked into a committed file")
            self.assertNotIn(str(Path.home()), written)

    def test_the_file_is_identical_whether_ci_or_a_developer_wrote_it(self) -> None:
        """Churn is how a committed file gets gitignored again."""
        import os as _os
        import unittest.mock as _mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            cache = root / ".buildanchor" / "verified.json"
            with _mock.patch.dict(_os.environ, {"CI": "true"}):
                BuildAnchor(str(root)).verify_commands(level="resolvable")
            from_ci = cache.read_bytes()
            environment = {k: v for k, v in _os.environ.items() if k != "CI"}
            with _mock.patch.dict(_os.environ, environment, clear=True):
                BuildAnchor(str(root)).verify_commands(level="resolvable")
            self.assertEqual(cache.read_bytes(), from_ci,
                             "the same result written by CI and by a developer differ")

    def test_full_run_duration_is_recorded_and_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            if not _real_venv_layout(root):
                self.skipTest("no pytest-bearing interpreter to probe with")
            result = BuildAnchor(str(root)).verify_commands(level="passes")
            entry = result["results"][0]
            self.assertEqual(entry["outcome"], "passes")
            self.assertIsNotNone(entry["full_run_duration_ms"])

            replayed = BuildAnchor(str(root)).inspect().module_details
            resolved = BuildAnchor(str(root)).resolve_command("test")
            self.assertIsNotNone(resolved.get("command_duration_ms") if replayed else 0)


class RootProjectVerificationTests(unittest.TestCase):
    """The root project's command is the one that matters most; verify it."""

    def test_root_command_is_verified_alongside_satellites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "core"\nversion = "1"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
            (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
            sdk = root / "sdk" / "node"
            sdk.mkdir(parents=True)
            (sdk / "package.json").write_text(
                json.dumps({"name": "sdk", "scripts": {"test": "node --test"}}), encoding="utf-8")

            result = BuildAnchor(str(root)).verify_commands(level="resolvable", write_cache=False)
            paths = [entry["path"] for entry in result["results"]]
            self.assertIn(".", paths, "the root project's own command was never verified")
            self.assertIn("sdk/node", paths)

    def test_a_monorepo_has_no_root_command_to_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b"):
                (root / name).mkdir()
                (root / name / "pyproject.toml").write_text(
                    f'[project]\nname = "{name}"\nversion = "1"\n', encoding="utf-8")
            result = BuildAnchor(str(root)).verify_commands(level="resolvable", write_cache=False)
            self.assertNotIn(".", [entry["path"] for entry in result["results"]])


class CachePruningTests(unittest.TestCase):
    def test_entries_for_vanished_modules_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("keep", "remove"):
                (root / name).mkdir()
                (root / name / "package.json").write_text(
                    json.dumps({"name": name, "scripts": {"test": "node --test"}}), encoding="utf-8")
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            cache_path = root / ".buildanchor" / "verified.json"
            self.assertIn("remove::test", json.loads(cache_path.read_text(encoding="utf-8"))["entries"])

            import shutil as _shutil
            _shutil.rmtree(root / "remove")
            BuildAnchor(str(root)).verify_commands(level="resolvable")
            entries = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
            self.assertNotIn("remove::test", entries, "a dead entry survived into a committed file")
            self.assertIn("keep::test", entries)

    def test_a_scoped_run_does_not_prune_other_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("alpha", "beta"):
                (root / name).mkdir()
                (root / name / "package.json").write_text(
                    json.dumps({"name": name, "scripts": {"test": "node --test"}}), encoding="utf-8")
            engine = BuildAnchor(str(root))
            engine.verify_commands(level="resolvable")
            BuildAnchor(str(root)).verify_commands(level="resolvable", scope="alpha")
            entries = json.loads((root / ".buildanchor" / "verified.json").read_text(encoding="utf-8"))["entries"]
            self.assertIn("beta::test", entries, "a scoped run pruned a module it never looked at")
