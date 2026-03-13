"""Patch cloned KIS_MCP_Server sources for MOMO runtime expectations."""
from pathlib import Path
import sys


def patch_server(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    old = '"ACNT_PRDT_CD": "01"'
    new = '"ACNT_PRDT_CD": os.environ.get("KIS_PROD_TYPE", "01")'
    count = text.count(old)
    if count == 0:
        raise RuntimeError("server.py에서 ACNT_PRDT_CD 하드코딩을 찾지 못했습니다.")
    path.write_text(text.replace(old, new), encoding="utf-8")
    return count


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: patch_server.py /path/to/server.py")
    count = patch_server(Path(argv[1]))
    print(f"patched ACNT_PRDT_CD occurrences: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
