"""Tests for _stream.StreamProcessor — NDJSON parsing for each backend's stream."""

from __future__ import annotations

from _stream import StreamProcessor


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

    def test_grok_stream(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "thought", "data": "ignored"}')
        assert not processor.process_line('{"type": "text", "data": "part1"}')
        assert not processor.process_line('{"type": "text", "data": "part2"}')
        assert processor.process_line(
            '{"type": "end", "stopReason": "EndTurn", "sessionId": "s", "requestId": "r"}'
        )
        result = processor.get_result()
        assert result["result"] == "part1part2"

    def test_grok_complete_json_output(self):
        processor = StreamProcessor()
        assert processor.process_complete_output(
            '{\n'
            '  "text": "{\\"findings\\":[]}",\n'
            '  "stopReason": "EndTurn",\n'
            '  "sessionId": "s",\n'
            '  "requestId": "r"\n'
            "}"
        )
        result = processor.get_result()
        assert result["result"] == '{"findings":[]}'
        assert result["status"] == "success"

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

    def test_grok_stream_extracts_trailing_json_result(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "text", "data": "I will review."}')
        assert not processor.process_line('{"type": "text", "data": "{\\\"findings\\\":[]}"}')
        assert processor.process_line('{"type": "end", "stopReason": "EndTurn"}')
        result = processor.get_result()
        assert result["result"] == '{"findings":[]}'
