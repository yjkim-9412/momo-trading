from analysis.llm.codex_cli_provider import CodexCLIProvider


def test_parse_event_stream_extracts_session_usage_and_text():
    raw_stdout = """
{"type":"thread.started","thread_id":"thread-123"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"RESULT"}}
{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":3}}
    """.strip()

    parsed = CodexCLIProvider.parse_event_stream(raw_stdout)

    assert parsed["session_id"] == "thread-123"
    assert parsed["fallback_text"] == "RESULT"
    assert parsed["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 3,
    }
