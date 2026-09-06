from __future__ import annotations

import json
from typing import Callable

from _constants import SUPPORTED_CLIS_HELP

# One decoded line from a backend's stream; every field is checked before use.
StreamData = dict[str, object]


def _is_string_delimiter(text: str, index: int) -> bool:
    """A quote is a delimiter unless an odd number of backslashes escape it."""
    backslashes = 0
    position = index - 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 0


def _trailing_object_start(text: str) -> int:
    """Index of the '{' opening the object that closes the text, or -1."""
    depth = 0
    in_string = False
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == '"' and _is_string_delimiter(text, index):
            in_string = not in_string
        elif in_string:
            continue
        elif char == "}":
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _extract_trailing_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped.endswith("}"):
        return text

    start = _trailing_object_start(stripped)
    if start < 0:
        return text

    try:
        value, end = json.JSONDecoder().raw_decode(stripped, start)
    except json.JSONDecodeError:
        return text
    if isinstance(value, dict) and end == len(stripped):
        return stripped[start:end]
    return text


def _grok_json_result(data: StreamData) -> StreamData | None:
    text = data.get("text")
    if not isinstance(text, str):
        return None
    return {
        "type": "result",
        "result": _extract_trailing_json_object(text),
        "status": "success" if data.get("stopReason") == "EndTurn" else "partial",
        "stop_reason": data.get("stopReason"),
        "session_id": data.get("sessionId"),
    }


class StreamProcessor:
    """Normalize supported CLI streams into a result payload."""

    def __init__(self, cli: str) -> None:
        self.cli = cli
        try:
            self._line_processor = _LINE_PROCESSORS[cli]
        except KeyError as e:
            raise ValueError(
                f"Unsupported CLI {cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}."
            ) from e
        self.result_json: StreamData | None = None
        self.gemini_parts: list[str] = []
        self.codex_messages: list[str] = []
        self.opencode_parts: list[str] = []

    def _process_gemini_line(self, data: StreamData) -> bool:
        if data.get("type") == "message" and data.get("role") == "assistant":
            content = data.get("content", "")
            if isinstance(content, str):
                self.gemini_parts.append(content)
            return False

        if data.get("type") == "result":
            self.result_json = {
                "type": "result",
                "result": "".join(self.gemini_parts),
                "status": data.get("status", "success"),
            }
            return True

        return False

    def _process_antigravity_line(self, data: StreamData) -> bool:
        if data.get("event") != "result":
            return False

        payload = data.get("result")
        if not isinstance(payload, dict):
            return False
        response = payload.get("response")
        if not isinstance(response, str):
            return False

        status = payload.get("status")
        if status == "SUCCESS":
            normalized_status = "success"
        elif status in ("CANCELED", "INTERRUPTED", "WAITING", "RUNNING"):
            normalized_status = "partial"
        else:
            normalized_status = "error"
        self.result_json = {
            "type": "result",
            "result": response,
            "status": normalized_status,
        }
        error = payload.get("error")
        if isinstance(error, str):
            self.result_json["error"] = error
        return True

    def _process_codex_line(self, data: StreamData) -> bool:
        if data.get("type") == "item.completed":
            item = data.get("item")
            if not isinstance(item, dict):
                return False
            text = item.get("text")
            if item.get("type") == "agent_message" and isinstance(text, str):
                self.codex_messages.append(text)
            return False

        if data.get("type") == "turn.completed":
            self.result_json = {
                "type": "result",
                "result": "\n".join(self.codex_messages),
                "status": "success",
            }
            return True

        return False

    def _process_opencode_line(self, data: StreamData) -> bool:
        part = data.get("part")
        if not isinstance(part, dict):
            return False

        if data.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                self.opencode_parts.append(text)
            return False

        if data.get("type") != "step_finish":
            return False

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

    def _process_command_code_line(self, data: StreamData) -> bool:
        if data.get("type") != "result":
            return False

        subtype = data.get("subtype")
        if subtype == "success":
            status = "success"
        elif subtype == "max_turns":
            status = "partial"
        else:
            status = "error"

        final_text = data.get("finalText")
        result: StreamData = {
            "type": "result",
            "result": final_text if isinstance(final_text, str) else "",
            "status": status,
        }
        for source_key, target_key in (
            ("stopReason", "stop_reason"),
            ("sessionId", "session_id"),
            ("error", "error"),
        ):
            value = data.get(source_key)
            if isinstance(value, str):
                result[target_key] = value
        self.result_json = result
        return True

    def _process_grok_line(self, data: StreamData) -> bool:
        grok_result = _grok_json_result(data)
        if grok_result is None:
            return False
        self.result_json = grok_result
        return True

    def _process_result_line(self, data: StreamData) -> bool:
        if data.get("type") != "result":
            return False

        subtype = data.get("subtype")
        is_error = (
            data.get("is_error") is True
            or data.get("status") == "error"
            or (isinstance(subtype, str) and subtype.startswith("error_"))
        )
        if is_error:
            self.result_json = {**data, "status": "error"}
            return True

        if not isinstance(data.get("result"), str):
            return False
        self.result_json = data
        return True

    def process_line(self, line: str) -> bool:
        """Process one line. Returns True when a terminal event is reached."""
        line = line.strip()
        if not line or self.result_json is not None:
            return False

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return False

        # A line may decode to any JSON value; only an object carries an event.
        if not isinstance(data, dict):
            return False

        return self._line_processor(self, data)

    def process_complete_output(self, output: str) -> bool:
        """Process a complete non-NDJSON payload. Returns True when parsed."""
        if self.result_json is not None:
            return False

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return False

        if isinstance(data, dict) and self.cli == "grok":
            return self._process_grok_line(data)

        return False

    def get_result(self) -> StreamData | None:
        return self.result_json


_LINE_PROCESSORS: dict[str, Callable[[StreamProcessor, StreamData], bool]] = {
    "codex": StreamProcessor._process_codex_line,
    "claude": StreamProcessor._process_result_line,
    "cursor-agent": StreamProcessor._process_result_line,
    "glm": StreamProcessor._process_result_line,
    "kimi": StreamProcessor._process_result_line,
    "grok": StreamProcessor._process_grok_line,
    "antigravity": StreamProcessor._process_antigravity_line,
    "gemini": StreamProcessor._process_gemini_line,
    "opencode": StreamProcessor._process_opencode_line,
    "command-code": StreamProcessor._process_command_code_line,
}
