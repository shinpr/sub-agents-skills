"""StreamProcessor: parse newline-delimited JSON output from various CLIs."""

from __future__ import annotations

import json


def _extract_trailing_json_object(text: str) -> str:
    """Return a trailing JSON object from text when one cleanly exists."""
    stripped = text.strip()
    if not stripped:
        return text

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and stripped[index + end :].strip() == "":
            return stripped[index : index + end]
    return text


class StreamProcessor:
    """Process streaming JSON output from various CLIs.

    Recognized formats:
    - Claude / Cursor: a single ``{"type": "result", "result": ...}`` line
    - Gemini stream: ``init`` then assistant ``message`` lines, ending with ``result``
    - Codex stream: ``thread.started`` then ``item.completed`` lines, ending with ``turn.completed``
    - Grok stream: ``text`` chunks, ending with ``end``
    """

    def __init__(self):
        self.result_json = None
        self.gemini_parts = []
        self.codex_messages = []
        self.grok_parts = []
        self.is_gemini = False
        self.is_codex = False
        self.is_grok = False

    def process_line(self, line: str) -> bool:
        """Process one line. Returns True when a terminal event is reached."""
        line = line.strip()
        if not line or self.result_json is not None:
            return False

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return False

        if data.get("type") == "init":
            self.is_gemini = True
            return False

        if data.get("type") == "thread.started":
            self.is_codex = True
            return False

        if self.is_gemini and data.get("type") == "message" and data.get("role") == "assistant":
            content = data.get("content", "")
            if isinstance(content, str):
                self.gemini_parts.append(content)
            return False

        if self.is_codex and data.get("type") == "item.completed":
            item = data.get("item", {})
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                self.codex_messages.append(item["text"])
            return False

        if data.get("type") == "text":
            content = data.get("data", "")
            if isinstance(content, str):
                self.is_grok = True
                self.grok_parts.append(content)
            return False

        if self.is_grok and data.get("type") == "end":
            self.result_json = {
                "type": "result",
                "result": _extract_trailing_json_object("".join(self.grok_parts)),
                "status": "success",
            }
            return True

        # Codex: turn.completed signals end
        if self.is_codex and data.get("type") == "turn.completed":
            self.result_json = {
                "type": "result",
                "result": "\n".join(self.codex_messages),
                "status": "success",
            }
            return True

        # Result type signals completion
        if data.get("type") == "result":
            if self.is_gemini:
                self.result_json = {
                    "type": "result",
                    "result": "".join(self.gemini_parts),
                    "status": data.get("status", "success"),
                }
            else:
                self.result_json = data
            return True

        # Fallback: first valid JSON without type field
        if "type" not in data:
            self.result_json = data
            return True

        return False

    def process_complete_output(self, output: str) -> bool:
        """Process a complete non-NDJSON payload. Returns True when parsed."""
        if self.result_json is not None:
            return False

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return False

        if isinstance(data, dict) and isinstance(data.get("text"), str):
            self.result_json = {
                "type": "result",
                "result": _extract_trailing_json_object(data["text"]),
                "status": "success" if data.get("stopReason") == "EndTurn" else "partial",
                "stop_reason": data.get("stopReason"),
                "session_id": data.get("sessionId"),
            }
            return True

        return False

    def get_result(self):
        return self.result_json
