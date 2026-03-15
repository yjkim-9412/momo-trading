import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "docker" / "kis-mcp" / "patch_server.py"
)

spec = importlib.util.spec_from_file_location("kis_mcp_patch_server", MODULE_PATH)
patch_server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(patch_server_module)


class KisMcpPatchServerTest(unittest.TestCase):
    def test_patch_server_updates_account_code_and_token_logic(self):
        original = (
            patch_server_module.IMPORT_OLD
            + "import os\nimport sys\nfrom pathlib import Path\n"
            + "from datetime import datetime, timedelta\n\n"
            + "import httpx\n\n"
            + patch_server_module.TOKEN_BLOCK_OLD
            + '\nrequest = {"ACNT_PRDT_CD": "01"}\n'
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            server_path = Path(temp_dir) / "server.py"
            server_path.write_text(original, encoding="utf-8")

            patched_count = patch_server_module.patch_server(server_path)
            patched = server_path.read_text(encoding="utf-8")

        self.assertEqual(patched_count, 1)
        self.assertIn("import asyncio", patched)
        self.assertIn("_token_lock = asyncio.Lock()", patched)
        self.assertIn("retrying after 60 seconds", patched)
        self.assertIn(
            '"ACNT_PRDT_CD": os.environ.get("KIS_PROD_TYPE", "01")',
            patched,
        )


if __name__ == "__main__":
    unittest.main()
