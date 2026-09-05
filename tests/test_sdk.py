import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from buildanchor import AsyncBuildAnchorClient, BuildAnchorClient, BuildAnchorHTTPError


class SDKTests(unittest.TestCase):
    def test_python_sync_and_async_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}), encoding="utf-8")
            client = BuildAnchorClient(str(root))
            self.assertEqual(client.inspect()["status"], "valid")
            self.assertEqual(client.context()["schema_version"], "v1")
            self.assertIn("content", client.llm_prompt("Add a feature"))
            self.assertIn("recommended_tool", client.token_estimate())
            self.assertTrue(client.preflight("Add a feature")["ready_to_act"])
            self.assertEqual(client.plan("Add a feature")["status"], "ready")
            self.assertEqual(client.compatibility()["schema_version"], "v1")
            self.assertEqual(client.find_package("missing-package")["package"], "missing-package")
            self.assertIn("modules", client.modules())
            self.assertEqual(client.resolve_command("test")["phase"], "test")
            self.assertIn("status", client.change_impact(staged=True))
            self.assertIn("status", client.validate_change("HEAD", False, 15, True))
            result = asyncio.run(self._async_check(str(root)))
            self.assertEqual(result["schema_version"], "v1")

    async def _async_check(self, root: str):
        async with AsyncBuildAnchorClient(root) as client:
            await client.llm_prompt("Add a feature")
            await client.find_package("missing-package")
            await client.modules()
            await client.validate_change("HEAD", False, 15, True)
            return await client.preflight("Add a feature")

    def test_python_http_client_uses_v1_routes_and_carries_workspace_on_every_call(self) -> None:
        class Response:
            def __init__(self, value: dict) -> None:
                self.value = value

            def read(self) -> bytes:
                return json.dumps(self.value).encode("utf-8")

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, exc_type, exc, traceback) -> bool:
                return False

        requests: list[tuple[str, dict, float]] = []

        def urlopen(request, timeout: float):
            payload = json.loads(request.data)
            requests.append((request.full_url, payload, timeout))
            if payload["workspace"] == "..":
                raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"status":"blocked"}'))
            return Response({"schema_version": "v1", "status": "valid"})

        with mock.patch("buildanchor.sdk.urllib.request.urlopen", side_effect=urlopen):
            client = BuildAnchorClient(workspace=".", endpoint="http://buildanchor.test", request_timeout_seconds=12)
            self.assertEqual(client.inspect(freshness="refresh")["schema_version"], "v1")
            self.assertEqual(client.compatibility()["status"], "valid")
            self.assertEqual(client.find_package("express")["schema_version"], "v1")
            self.assertEqual(client.resolve_command("test")["schema_version"], "v1")
            self.assertEqual(client.validate_change(staged=True)["schema_version"], "v1")
            with self.assertRaises(BuildAnchorHTTPError) as raised:
                BuildAnchorClient(workspace="..", endpoint="http://buildanchor.test").inspect()

        self.assertEqual(raised.exception.status_code, 400)
        self.assertTrue(all(payload["workspace"] == "." for _, payload, _ in requests[:-1]))
        self.assertTrue(any(url.endswith("/v1/compatibility") for url, _, _ in requests))
        self.assertTrue(all(timeout == 12 for _, _, timeout in requests[:-1]))


if __name__ == "__main__":
    unittest.main()
