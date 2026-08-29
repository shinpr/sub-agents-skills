"""Tests for _stream.StreamProcessor — NDJSON parsing for each backend's stream."""

from __future__ import annotations

import json

import pytest
from _constants import SUPPORTED_CLIS
from _stream import _LINE_PROCESSORS, StreamProcessor, _extract_trailing_json_object


class TestStreamProcessor:
    def test_supported_clis_have_line_processors(self):
        assert set(_LINE_PROCESSORS) == set(SUPPORTED_CLIS)

    @pytest.mark.parametrize("cli", ["claude", "glm", "kimi"])
    def test_claude_family_result(self, cli):
        processor = StreamProcessor(cli)
        assert processor.process_line('{"type": "result", "result": "hello"}')
        result = processor.get_result()
        assert result["result"] == "hello"

    @pytest.mark.parametrize("cli", ["claude", "glm", "kimi"])
    def test_claude_family_error_result(self, cli):
        processor = StreamProcessor(cli)
        assert processor.process_line(
            '{"type":"result","subtype":"error_during_execution","is_error":true}'
        )
        result = processor.get_result()
        assert result["status"] == "error"

    @pytest.mark.parametrize("cli", ["claude", "glm", "kimi"])
    def test_claude_family_success_result_requires_text(self, cli):
        processor = StreamProcessor(cli)
        assert not processor.process_line('{"type":"result","subtype":"success","is_error":false}')
        assert processor.get_result() is None

    def test_claude_non_result_event_with_text_is_not_terminal(self):
        processor = StreamProcessor("claude")
        assert not processor.process_line(
            '{"type": "system", "subtype": "notification", '
            '"text": "Background operation completed"}'
        )
        assert processor.get_result() is None

        assert processor.process_line('{"type": "result", "result": "actual response"}')
        assert processor.get_result()["result"] == "actual response"

    def test_gemini_stream(self):
        processor = StreamProcessor("gemini")
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

    def test_antigravity_stream(self):
        processor = StreamProcessor("antigravity")
        assert not processor.process_line('{"event":"init","conversation_id":"c1"}')
        assert not processor.process_line(
            '{"event":"step_update","step_update":{"text_delta":"part1"}}'
        )
        assert processor.process_line(
            '{"event":"result","result":{"status":"SUCCESS","response":"done"}}'
        )
        result = processor.get_result()
        assert result["result"] == "done"
        assert result["status"] == "success"

    def test_antigravity_error(self):
        processor = StreamProcessor("antigravity")
        assert processor.process_line(
            '{"event":"result","result":{"status":"ERROR","response":"",'
            '"error":"authentication required"}}'
        )
        result = processor.get_result()
        assert result["status"] == "error"
        assert result["error"] == "authentication required"

    @pytest.mark.parametrize("status", ["CANCELED", "INTERRUPTED", "WAITING", "RUNNING"])
    def test_antigravity_incomplete_status_is_partial(self, status):
        processor = StreamProcessor("antigravity")
        assert processor.process_line(
            json.dumps({"event": "result", "result": {"status": status, "response": "progress"}})
        )
        assert processor.get_result()["status"] == "partial"

    def test_antigravity_invalid_status_is_error(self):
        processor = StreamProcessor("antigravity")
        assert processor.process_line(
            '{"event":"result","result":{"status":"INVALID","response":""}}'
        )
        assert processor.get_result()["status"] == "error"

    def test_codex_stream(self):
        processor = StreamProcessor("codex")
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
        processor = StreamProcessor("opencode")
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
        processor = StreamProcessor("opencode")
        assert not processor.process_line('{"type":"text","part":{"text":"truncated"}}')
        assert processor.process_line('{"type":"step_finish","part":{"reason":"length"}}')
        result = processor.get_result()
        assert result["result"] == "truncated"
        assert result["status"] == "partial"

    def test_command_code_success_result(self):
        processor = StreamProcessor("command-code")
        assert not processor.process_line(
            '{"type":"event","event":{"type":"tool_running","toolName":"read_file"}}'
        )
        assert processor.process_line(
            '{"type":"result","subtype":"success","sessionId":"session-1",'
            '"stopReason":"end_turn","finalText":"DONE","usage":{},"durationMs":12}'
        )
        assert processor.get_result() == {
            "type": "result",
            "result": "DONE",
            "status": "success",
            "stop_reason": "end_turn",
            "session_id": "session-1",
        }

    def test_command_code_max_turns_result_is_partial(self):
        processor = StreamProcessor("command-code")
        assert processor.process_line(
            '{"type":"result","subtype":"max_turns","stopReason":"max_turns",'
            '"finalText":"progress","usage":{},"durationMs":12}'
        )
        assert processor.get_result() == {
            "type": "result",
            "result": "progress",
            "status": "partial",
            "stop_reason": "max_turns",
        }

    def test_command_code_error_result(self):
        processor = StreamProcessor("command-code")
        assert processor.process_line(
            '{"type":"result","subtype":"error","finalText":"",'
            '"error":"Not authenticated","usage":{},"durationMs":2}'
        )
        assert processor.get_result() == {
            "type": "result",
            "result": "",
            "status": "error",
            "error": "Not authenticated",
        }

    def test_grok_complete_json_output(self):
        processor = StreamProcessor("grok")
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
        processor = StreamProcessor("grok")
        assert processor.process_line('{"text": "{\\"findings\\":[]}", "stopReason": "EndTurn"}')
        result = processor.get_result()
        assert result["type"] == "result"
        assert result["result"] == '{"findings":[]}'
        assert result["status"] == "success"

    def test_grok_text_without_stop_reason_is_partial(self):
        processor = StreamProcessor("grok")
        assert processor.process_line('{"text": "final answer"}')
        result = processor.get_result()
        assert result["result"] == "final answer"
        assert result["status"] == "partial"

    def test_grok_compact_json_line_cancelled_is_partial(self):
        processor = StreamProcessor("grok")
        assert processor.process_line('{"text": "progress only", "stopReason": "Cancelled"}')
        result = processor.get_result()
        assert result["result"] == "progress only"
        assert result["status"] == "partial"

    def test_cursor_typeless_json_is_not_terminal(self):
        processor = StreamProcessor("cursor-agent")
        assert not processor.process_line('{"message": "raw"}')
        assert processor.get_result() is None

    def test_cursor_typed_result(self):
        processor = StreamProcessor("cursor-agent")
        assert processor.process_line(
            '{"type":"result","subtype":"success","is_error":false,"result":"done"}'
        )
        assert processor.get_result()["result"] == "done"

    def test_unknown_cli_fails_fast(self):
        with pytest.raises(ValueError, match="Unsupported CLI"):
            StreamProcessor("unknown")

    def test_grok_complete_json_cancelled_is_partial(self):
        processor = StreamProcessor("grok")
        assert processor.process_complete_output(
            '{"text": "progress only", "stopReason": "Cancelled"}'
        )
        result = processor.get_result()
        assert result["result"] == "progress only"
        assert result["status"] == "partial"

    def test_grok_complete_json_extracts_trailing_json_result(self):
        processor = StreamProcessor("grok")
        assert processor.process_complete_output(
            '{"text": "I will review.{\\"findings\\":[]}", "stopReason": "EndTurn"}'
        )
        result = processor.get_result()
        assert result["result"] == '{"findings":[]}'

    def test_extract_trailing_json_object_rejects_extra_suffix(self):
        text = 'prefix {"findings":[]} trailing'
        assert _extract_trailing_json_object(text) == text


class TestExtractTrailingJsonObject:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", ""),
            ("   ", "   "),
            ("no json here", "no json here"),
            ('{"a":1}', '{"a":1}'),
            ('  {"a":1}\n\n', '{"a":1}'),
            ('prose {"a":{"b":[1,{"c":2}]}}', '{"a":{"b":[1,{"c":2}]}}'),
            ('prose {"a":"{ not real }"}', '{"a":"{ not real }"}'),
            (r'prose {"a":"quote \" brace {"}', r'{"a":"quote \" brace {"}'),
            (r'prose {"a":"odd \\\" still string {"}', r'{"a":"odd \\\" still string {"}'),
            (r'prose {"a":"even \\"}', r'{"a":"even \\"}'),
            ('unbalanced { and " in prose {"a":1}', '{"a":1}'),
            ('{"a":1} {"b":2}', '{"b":2}'),
            ('{"a":1} trailing', '{"a":1} trailing'),
            ("prose [1,2]", "prose [1,2]"),
            ("prose {broken", "prose {broken"),
            ('prose {"a":1', 'prose {"a":1'),
        ],
    )
    def test_extraction_cases(self, text, expected):
        assert _extract_trailing_json_object(text) == expected

    def test_invalid_input_returns_original_text_with_whitespace(self):
        text = "  no trailing object }  "
        assert _extract_trailing_json_object(text) == text

    def test_scans_candidates_without_repeated_decoding(self, monkeypatch):
        calls = []
        original = json.JSONDecoder.raw_decode

        def counting_raw_decode(self, s, idx=0):
            calls.append(idx)
            return original(self, s, idx)

        monkeypatch.setattr(json.JSONDecoder, "raw_decode", counting_raw_decode)
        text = "{x " * 20000 + "}"
        assert _extract_trailing_json_object(text) == text
        assert len(calls) <= 1
