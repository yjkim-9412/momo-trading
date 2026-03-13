"""KIS MCP Server entrypoint — httpcore/httpx DEBUG 로그 억제 후 FastMCP 실행."""
from collections.abc import MutableMapping
import logging
import os
import sys


def _split_account_number(raw: str) -> tuple[str, str]:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 10:
        raise ValueError("KIS 계좌번호는 10자리 전체 또는 CANO 8자리 + KIS_PROD_TYPE 이 필요합니다.")
    return digits[:8], digits[8:10]


def _resolve_account_parts(raw: str, prod_type_override: str = "") -> tuple[str, str]:
    digits = "".join(ch for ch in raw if ch.isdigit())
    prod_digits = "".join(ch for ch in prod_type_override if ch.isdigit())

    if prod_digits and len(prod_digits) != 2:
        raise ValueError("KIS_PROD_TYPE 는 2자리여야 합니다.")

    if len(digits) == 10:
        cano, default_prod_type = _split_account_number(raw)
        return cano, prod_digits or default_prod_type

    if len(digits) == 8 and prod_digits:
        return digits, prod_digits

    raise ValueError("KIS 계좌번호는 10자리 전체 또는 CANO 8자리 + KIS_PROD_TYPE 이 필요합니다.")


def configure_runtime_env(env: MutableMapping[str, str] | None = None) -> None:
    target = env if env is not None else os.environ
    account_type = target.get("KIS_ACCOUNT_TYPE", "VIRTUAL").upper()

    if account_type == "VIRTUAL":
        for real, paper in [
            ("KIS_APP_KEY", "KIS_PAPER_APP_KEY"),
            ("KIS_APP_SECRET", "KIS_PAPER_APP_SECRET"),
        ]:
            value = target.get(paper)
            if value:
                target[real] = value

    if account_type == "VIRTUAL":
        raw_account = target.get("KIS_PAPER_STOCK") or target.get("KIS_PAPER_CANO")
    else:
        raw_account = target.get("KIS_ACCT_STOCK") or target.get("KIS_CANO")
    if not raw_account:
        raise ValueError("활성 KIS 계좌번호가 없습니다. KIS_ACCT_STOCK 또는 KIS_PAPER_STOCK 를 설정하세요.")

    cano, prod_type = _resolve_account_parts(raw_account, target.get("KIS_PROD_TYPE", ""))
    target["KIS_CANO"] = cano
    target["KIS_PROD_TYPE"] = prod_type


def configure_logging() -> None:
    for name in ("httpcore", "httpx", "hpack", "h11", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.WARNING)


if __name__ == "__main__":
    configure_runtime_env()
    configure_logging()

    from fastmcp.cli import app as typer_app

    sys.argv = [
        "fastmcp", "run", "server.py:mcp",
        "--transport", "sse",
        "--host", "0.0.0.0",
        "--port", "3000",
    ]
    typer_app()
