from __future__ import annotations

import json


def _extract_trailing_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return text

    decoder = json.JSONDecoder()
    for index in range(len(stripped) - 1, -1, -1):
        if stripped[index] != "{":
            continue
        try:
            value, end = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and stripped[end:].strip() == "":
            return stripped[index:end]
    return text


def _grok_json_result(data: dict) -> dict | None:
    # "stopReason" is grok's own completion marker. Without it, an unrelated
    # JSON line that happens to carry a string "text" field (e.g. another
    # backend's system notification) would be misidentified as a grok result
    # and short-circuit the real terminal event.
    if "stopReason" not in data or not isinstance(data.get("text"), str):
        return None
    return {
        "type": "result",
        "result": _extract_trailing_json_object(data["text"]),
        "status": "success" if data.get("stopReason") == "EndTurn" else "partial",
        "stop_reason": data.get("stopReason"),
        "session_id": data.get("sessionId"),
    }


def _kimi_cli_json_result(data: dict) -> dict | None:
    # Native Kimi Code CLI --output-format stream-json emits one JSON object
    # per turn with no "type" field: {"role": "assistant", "tool_calls": [...]}
    # for intermediate tool-call turns (no "content" key), and
    # {"role": "assistant", "content": "..."} for the final answer. Requiring
    # a string "content" skips the intermediate tool-call turns.
    if data.get("role") != "assistant" or not isinstance(data.get("content"), str):
        return None
    return {"type": "result", "result": data["content"], "status": "success"}


def _agy_json_result(data: dict) -> dict | None:
    # agy's --output-format json is a single blob with no "type" field:
    # {"conversation_id": ..., "status": "SUCCESS", "response": "...", ...}
    if "conversation_id" not in data or not isinstance(data.get("response"), str):
        return None
    return {
        "type": "result",
        "result": data["response"],
        "status": "success" if data.get("status") == "SUCCESS" else "partial",
        "conversation_id": data.get("conversation_id"),
    }


class StreamProcessor:
    """Normalize supported CLI streams into a result payload."""

    def __init__(self):
        self.result_json = None
        self.gemini_parts = []
        self.codex_messages = []
        self.opencode_parts = []
        self.is_gemini = False
        self.is_codex = False
        self.is_opencode = False

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

        part = data.get("part")
        if data.get("type") in {"step_start", "tool_use", "text", "step_finish"} and isinstance(
            part, dict
        ):
            self.is_opencode = True

        if self.is_opencode and data.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                self.opencode_parts.append(text)
            return False

        if self.is_opencode and data.get("type") == "step_finish":
            reason = part.get("reason")
            if reason == "tool-calls" or reason is None:
                return False
            self.result_json = {
                "type": "result",
                "result": "".join(self.opencode_parts),
                "status": "success" if reason == "stop" else "partial",
                "stop_reason": reason,
            }
            return True

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

        if self.is_codex and data.get("type") == "turn.completed":
            self.result_json = {
                "type": "result",
                "result": "\n".join(self.codex_messages),
                "status": "success",
            }
            return True

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

        grok_result = _grok_json_result(data)
        if grok_result is not None:
            self.result_json = grok_result
            return True

        agy_result = _agy_json_result(data)
        if agy_result is not None:
            self.result_json = agy_result
            return True

        kimi_cli_result = _kimi_cli_json_result(data)
        if kimi_cli_result is not None:
            self.result_json = kimi_cli_result
            return True

        if "role" in data:
            # Belongs to kimi-cli's per-turn shape (assistant tool-call
            # requests, tool results, trailing meta lines) but wasn't the
            # final answer above — consume it without treating it as terminal.
            return False

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

        if isinstance(data, dict):
            grok_result = _grok_json_result(data)
            if grok_result is not None:
                self.result_json = grok_result
                return True
            agy_result = _agy_json_result(data)
            if agy_result is not None:
                self.result_json = agy_result
                return True
            return False

        return False

    def get_result(self):
        return self.result_json
