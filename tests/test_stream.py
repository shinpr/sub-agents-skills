"""Tests for _stream.StreamProcessor — NDJSON parsing for each backend's stream."""

from __future__ import annotations

from _stream import StreamProcessor, _extract_trailing_json_object


class TestStreamProcessor:
    def test_claude_result(self):
        processor = StreamProcessor()
        assert processor.process_line('{"type": "result", "result": "hello"}')
        result = processor.get_result()
        assert result["result"] == "hello"

    def test_gemini_stream(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "init"}')
        assert not processor.process_line(
            '{"type": "message", "role": "assistant", "content": "part1"}'
        )
        assert not processor.process_line(
            '{"type": "message", "role": "assistant", "content": "part2"}'
        )
        assert processor.process_line('{"type": "result", "status": "success"}')
        result = processor.get_result()
        assert result["result"] == "part1part2"

    def test_codex_stream(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "thread.started"}')
        assert not processor.process_line(
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "msg1"}}'
        )
        assert not processor.process_line(
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "msg2"}}'
        )
        assert processor.process_line('{"type": "turn.completed"}')
        result = processor.get_result()
        assert result["result"] == "msg1\nmsg2"

    def test_opencode_stream_collects_text_until_stop(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type":"step_start","part":{}}')
        assert not processor.process_line('{"type":"text","part":{"text":"part1"}}')
        assert not processor.process_line('{"type":"step_finish","part":{"reason":"tool-calls"}}')
        assert not processor.process_line('{"type":"step_start","part":{}}')
        assert not processor.process_line('{"type":"text","part":{"text":"part2"}}')
        assert processor.process_line('{"type":"step_finish","part":{"reason":"stop"}}')
        result = processor.get_result()
        assert result["result"] == "part1part2"
        assert result["status"] == "success"
        assert result["stop_reason"] == "stop"

    def test_opencode_non_stop_finish_is_partial(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type":"text","part":{"text":"truncated"}}')
        assert processor.process_line('{"type":"step_finish","part":{"reason":"length"}}')
        result = processor.get_result()
        assert result["result"] == "truncated"
        assert result["status"] == "partial"

    def test_grok_complete_json_output(self):
        processor = StreamProcessor()
        assert processor.process_complete_output(
            "{\n"
            '  "text": "{\\"findings\\":[]}",\n'
            '  "stopReason": "EndTurn",\n'
            '  "sessionId": "s",\n'
            '  "requestId": "r"\n'
            "}"
        )
        result = processor.get_result()
        assert result["result"] == '{"findings":[]}'
        assert result["status"] == "success"

    def test_grok_compact_json_line_output(self):
        processor = StreamProcessor()
        assert processor.process_line('{"text": "{\\"findings\\":[]}", "stopReason": "EndTurn"}')
        result = processor.get_result()
        assert result["type"] == "result"
        assert result["result"] == '{"findings":[]}'
        assert result["status"] == "success"

    def test_grok_compact_json_line_cancelled_is_partial(self):
        processor = StreamProcessor()
        assert processor.process_line('{"text": "progress only", "stopReason": "Cancelled"}')
        result = processor.get_result()
        assert result["result"] == "progress only"
        assert result["status"] == "partial"

    def test_typeless_json_without_text_uses_fallback(self):
        processor = StreamProcessor()
        assert processor.process_line('{"message": "raw"}')
        assert processor.get_result() == {"message": "raw"}

    def test_unrelated_text_field_does_not_match_grok_parser(self):
        # A claude/glm/kimi system notification (e.g. a Stop hook error) can
        # carry a string "text" field with no "stopReason". Without the
        # stopReason guard, this used to be misidentified as a grok result
        # and short-circuit the real terminal event.
        processor = StreamProcessor()
        assert not processor.process_line(
            '{"type": "system", "subtype": "notification", "key": "stop-hook-error", '
            '"text": "Stop hook error occurred"}'
        )
        assert processor.get_result() is None

    def test_agy_json_line_result(self):
        processor = StreamProcessor()
        assert processor.process_line(
            '{"conversation_id": "c1", "status": "SUCCESS", "response": "OK.\\n"}'
        )
        result = processor.get_result()
        assert result["result"] == "OK.\n"
        assert result["status"] == "success"

    def test_agy_json_line_non_success_is_partial(self):
        processor = StreamProcessor()
        assert processor.process_line(
            '{"conversation_id": "c1", "status": "CANCELLED", "response": "partial"}'
        )
        result = processor.get_result()
        assert result["status"] == "partial"

    def test_agy_complete_json_output(self):
        processor = StreamProcessor()
        assert processor.process_complete_output(
            '{"conversation_id": "c1", "status": "SUCCESS", "response": "OK."}'
        )
        result = processor.get_result()
        assert result["result"] == "OK."

    def test_kimi_cli_skips_intermediate_tool_call_turn(self):
        processor = StreamProcessor()
        assert not processor.process_line(
            '{"role": "assistant", "tool_calls": [{"type": "function", '
            '"function": {"name": "Bash", "arguments": "{}"}}]}'
        )
        assert not processor.process_line(
            '{"role": "tool", "tool_call_id": "t1", "content": "file1\\nfile2"}'
        )
        assert processor.process_line('{"role": "assistant", "content": "There are 2 files."}')
        result = processor.get_result()
        assert result["result"] == "There are 2 files."
        assert result["status"] == "success"

    def test_kimi_cli_ignores_trailing_meta_line(self):
        processor = StreamProcessor()
        assert processor.process_line('{"role": "assistant", "content": "OK."}')
        assert not processor.process_line(
            '{"role": "meta", "type": "session.resume_hint", "session_id": "s1"}'
        )
        assert processor.get_result()["result"] == "OK."

    def test_grok_complete_json_cancelled_is_partial(self):
        processor = StreamProcessor()
        assert processor.process_complete_output(
            '{"text": "progress only", "stopReason": "Cancelled"}'
        )
        result = processor.get_result()
        assert result["result"] == "progress only"
        assert result["status"] == "partial"

    def test_grok_complete_json_extracts_trailing_json_result(self):
        processor = StreamProcessor()
        assert processor.process_complete_output(
            '{"text": "I will review.{\\"findings\\":[]}", "stopReason": "EndTurn"}'
        )
        result = processor.get_result()
        assert result["result"] == '{"findings":[]}'

    def test_extract_trailing_json_object_rejects_extra_suffix(self):
        text = 'prefix {"findings":[]} trailing'
        assert _extract_trailing_json_object(text) == text
