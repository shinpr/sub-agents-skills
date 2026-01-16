# CLI Output Formats Reference

Load this reference when debugging stream processing issues, selecting CLI for specific use cases, or understanding CLI-specific behaviors.

## CLI Comparison Matrix

| CLI | Output Format | Streaming | Result Extraction | Best For |
|-----|---------------|-----------|-------------------|----------|
| claude | stream-json | Yes | `type: "result"` object | General tasks, long outputs |
| cursor-agent | json | No | `type: "result"` object | Quick tasks, IDE integration |
| codex | event stream | Yes | `turn.completed` event | Code generation, tool use |
| gemini | NDJSON | Yes | Concatenate assistant messages | Multi-turn, conversational |

## Claude

**Command**: `claude --output-format stream-json --verbose -p "prompt"`

**Output Pattern**:
```json
{"type": "result", "result": "output text", "status": "success"}
```

**Characteristics**:
- Single JSON object with result
- `--verbose` required for proper stream flushing
- Supports `--settings` for custom configuration

## Cursor-Agent

**Command**: `cursor-agent --output-format json -p "prompt"`

**Output Pattern**:
```json
{"type": "result", "result": "output text", "status": "success"}
```

**Characteristics**:
- Same format as Claude
- Supports `-a` flag for API key
- Non-streaming (waits for complete response)

## Gemini

**Command**: `gemini --output-format stream-json -p "prompt"`

**Output Pattern** (NDJSON - one JSON per line):
```json
{"type": "init", ...}
{"type": "message", "role": "assistant", "content": "part 1"}
{"type": "message", "role": "assistant", "content": "part 2"}
{"type": "result", "status": "success"}
```

**Result Extraction**:
- Concatenate all `content` fields from `assistant` messages
- `type: "result"` signals completion (no result content in this line)

## Codex

**Command**: `codex exec --json "prompt"`

**Output Pattern** (Event stream):
```json
{"type": "thread.started", ...}
{"type": "item.completed", "item": {"type": "agent_message", "text": "part 1"}}
{"type": "item.completed", "item": {"type": "agent_message", "text": "part 2"}}
{"type": "turn.completed", "usage": {...}}
```

**Result Extraction**:
- Collect `text` from all `agent_message` items
- Join with newlines
- `turn.completed` signals completion

## Exit Codes

| Code | Meaning | Recoverable | Action |
|------|---------|-------------|--------|
| 0 | Success | - | Use result |
| 124 | Timeout | Yes | Retry with longer timeout |
| 127 | CLI not found | No | Install CLI |
| 143 | SIGTERM | Yes | Normal termination after result detected |
| 1 | General error | Maybe | Check stderr |

## Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Empty result | CLI not outputting JSON | Verify CLI version supports JSON output |
| Partial JSON | Stream interrupted | Check timeout, network stability |
| exit_code 127 | CLI not in PATH | Install CLI or set full path |
| Garbled output | Wrong output format flag | Check command matches CLI type |
