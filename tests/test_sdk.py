import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from buildanchor import AsyncBuildAnchorClient, BuildAnchorClient


class SDKTests(unittest.TestCase):
    def test_python_sync_and_async_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}), encoding="utf-8")
            client = BuildAnchorClient(str(root))
            self.assertEqual(client.inspect()["status"], "valid")
            self.assertEqual(client.context()["schema_version"], "v1")
            self.assertTrue(client.preflight("Add a feature")["ready_to_act"])
            self.assertEqual(client.plan("Add a feature")["status"], "ready")
            result = asyncio.run(self._async_check(str(root)))
            self.assertEqual(result["schema_version"], "v1")

    async def _async_check(self, root: str):
        async with AsyncBuildAnchorClient(root) as client:
            return await client.preflight("Add a feature")


if __name__ == "__main__":
    unittest.main()
