import importlib.util
from pathlib import Path

import pytest


ENTRYPOINT_PATH = Path(__file__).resolve().parents[1] / "docker" / "kis-mcp" / "entrypoint.py"
SPEC = importlib.util.spec_from_file_location("kis_mcp_entrypoint", ENTRYPOINT_PATH)
ENTRYPOINT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ENTRYPOINT)


def test_configure_runtime_env_splits_virtual_account_and_promotes_paper_credentials():
    env = {
        "KIS_ACCOUNT_TYPE": "VIRTUAL",
        "KIS_PAPER_APP_KEY": "paper-key",
        "KIS_PAPER_APP_SECRET": "paper-secret",
        "KIS_PAPER_CANO": "1234567801",
    }

    ENTRYPOINT.configure_runtime_env(env)

    assert env["KIS_APP_KEY"] == "paper-key"
    assert env["KIS_APP_SECRET"] == "paper-secret"
    assert env["KIS_CANO"] == "12345678"
    assert env["KIS_PROD_TYPE"] == "01"


def test_configure_runtime_env_requires_product_code_when_account_is_only_cano():
    env = {
        "KIS_ACCOUNT_TYPE": "REAL",
        "KIS_CANO": "12345678",
    }

    with pytest.raises(ValueError, match="KIS_PROD_TYPE"):
        ENTRYPOINT.configure_runtime_env(env)
