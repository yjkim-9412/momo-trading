from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_use_lf_line_endings() -> None:
    shell_scripts = sorted(ROOT.glob("*.sh"))
    assert shell_scripts, "expected shell scripts in the repository root"

    for script in shell_scripts:
        content = script.read_bytes()
        assert b"\r\n" not in content, f"{script.name} must use LF line endings"
        assert content.startswith(
            b"#!/usr/bin/env bash\n"
        ), f"{script.name} must keep an LF shebang"
