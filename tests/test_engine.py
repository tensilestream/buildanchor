import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from buildanchor import BuildAnchor, BuildAnchorError


class BuildAnchorEngineTests(unittest.TestCase):
    @staticmethod
    def _git(root: Path, *arguments: str) -> None:
        result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise AssertionError(result.stderr)

    def test_detects_all_first_mvp_ecosystems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in {
                "pom.xml": "<project><properties><java.version>21</java.version></properties></project>",
                "build.gradle.kts": "java { sourceCompatibility = JavaVersion.VERSION_21 }",
                "package.json": json.dumps({"engines": {"node": ">=22"}, "scripts": {"test": "jest"}}),
                "pyproject.toml": "[project]\nrequires-python = '>=3.12'\ndependencies = ['requests>=2']\n",
                "go.mod": "module example.com/app\ngo 1.23\nrequire example.com/lib v1.2.3\n",
                "Cargo.toml": "[package]\nedition = '2021'\n[dependencies]\nserde = '1'\n",
                "app.csproj": "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
                "Makefile": "test:\n\ttrue\n",
            }.items():
                (root / name).write_text(content, encoding="utf-8")
            report = BuildAnchor(root).inspect()
            self.assertEqual(report.status, "valid")
            self.assertTrue({"maven", "gradle", "node", "python", "go", "rust", "dotnet", "generic"}.issubset(set(report.build_systems)))
            self.assertTrue(any(fact.key == "runtime.java" for fact in report.facts))
            self.assertTrue(any(item["coordinate"] == "example.com/lib:v1.2.3" for item in report.dependencies))

    def test_context_is_compact_and_references_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}, "scripts": {"test": "npm test"}}), encoding="utf-8")
            engine = BuildAnchor(root)
            report = engine.inspect()
            context = engine.context(report, token_budget=100)
            self.assertEqual(context.schema_version, "v1")
            self.assertTrue(context.evidence_refs)
            self.assertLess(len(json.dumps(context.to_dict())), 2000)

    def test_rejects_workspace_outside_allow_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            with self.assertRaises(BuildAnchorError):
                BuildAnchor(child, root / "other")

    def test_recommends_jakarta_for_spring_boot_three(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text("<project><properties><java.version>17</java.version><spring-boot.version>3.4.2</spring-boot.version></properties></project>", encoding="utf-8")
            source = root / "UserEntity.java"
            source.write_text("import javax.persistence.Entity;\n@Entity class UserEntity {}\n", encoding="utf-8")
            report = BuildAnchor(root).inspect()
            self.assertEqual(report.status, "invalid")
            self.assertEqual(report.recommendations[0]["recommended"], "jakarta.persistence")
            self.assertEqual(report.recommendations[0]["dependency"], "jakarta.persistence:jakarta.persistence-api")
            self.assertIn("UserEntity.java", report.recommendations[0]["affected_files"])

    def test_preflight_blocks_incompatible_agent_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text("<project><properties><spring-boot.version>3.4.2</spring-boot.version></properties></project>", encoding="utf-8")
            (root / "UserEntity.java").write_text("import javax.persistence.Entity;\n", encoding="utf-8")
            preflight = BuildAnchor(root).preflight("Add a JPA entity")
            self.assertFalse(preflight["ready_to_act"])
            self.assertEqual(preflight["status"], "blocked")
            self.assertTrue(any("jakarta.persistence" in instruction for instruction in preflight["instructions"]))

    def test_plan_is_ready_before_a_compatible_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}), encoding="utf-8")
            plan = BuildAnchor(root).plan("Add a health endpoint")
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["steps"][0]["status"], "complete")
            self.assertEqual(plan["steps"][2]["id"], "act")

    def test_explains_missing_git_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
            result = BuildAnchor(root).validate_change()
            self.assertEqual(result["status"], "inconclusive")
            self.assertIn("No Git repository was detected", result["change"]["guidance"][0])
            self.assertFalse(result["report"]["git"]["detected"])

    def test_tracks_untracked_files_and_runs_bounded_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_smoke.py").write_text("import unittest\n\nclass Smoke(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", ".")
            self._git(root, "-c", "user.name=BuildAnchor Test", "-c", "user.email=buildanchor-test", "commit", "-qm", "baseline")
            (root / "notes.md").write_text("untracked change\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n\n# documented change\n", encoding="utf-8")

            result = BuildAnchor(root).validate_change(execute=True, timeout_seconds=30)

            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["change"]["status"], "inconclusive")
            self.assertIn({"status": "??", "path": "notes.md"}, result["change"]["changed_files"])
            self.assertEqual(result["execution"]["mode"], "probe")
            self.assertEqual(result["execution"]["results"][0]["status"], "passed")
            self.assertEqual(result["repair"]["issues"], [])

    def test_find_package_found_and_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"axios": "^1.7.0"},
            }), encoding="utf-8")
            engine = BuildAnchor(root)

            # Found declared package
            found_res = engine.find_package("axios")
            self.assertTrue(found_res["found"])
            self.assertEqual(found_res["results"][0]["declared_version"], "^1.7.0")
            self.assertFalse(found_res["results"][0]["installed"])

            # Not found package
            not_found = engine.find_package("definitely-missing-pkg-xyz")
            self.assertFalse(not_found["found"])
            self.assertEqual(not_found["results"], [])
            self.assertIn("not found", not_found["guidance"].lower())

            # installed_only flag
            installed_only = engine.find_package("axios", installed_only=True)
            self.assertFalse(installed_only["found"])

    def test_resolve_command_across_ecosystems(self) -> None:
        # Node with pnpm
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"test": "vitest", "build": "vite build"},
            }), encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
            engine = BuildAnchor(root)
            self.assertEqual(engine.resolve_command("test")["command"], "pnpm run test")
            self.assertEqual(engine.resolve_command("build")["command"], "pnpm run build")

        # Python with pytest in pyproject.toml
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
            engine = BuildAnchor(root)
            self.assertEqual(engine.resolve_command("test")["command"], "python -m pytest")

        # Maven pom.xml
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text("<project></project>", encoding="utf-8")
            engine = BuildAnchor(root)
            self.assertEqual(engine.resolve_command("test")["command"], "mvn test")
            self.assertEqual(engine.resolve_command("build")["command"], "mvn package")


if __name__ == "__main__":
    unittest.main()
