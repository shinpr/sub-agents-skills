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
