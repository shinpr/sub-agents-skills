import {existsSync, realpathSync} from "node:fs";
import {homedir} from "node:os";
import path from "node:path";

const PERMISSIONS = new Set(["read-only", "safe-edit", "yolo"]);

const CONTROL_TOOLS = new Set([
  "agent",
  "ask_user_question",
  "enter_worktree",
  "exit_worktree",
]);

const READ_ONLY_BLOCKED_TOOLS = new Set([
  "edit_file",
  "write_file",
  "shell_command",
  "powershell",
  "monitor_command",
  "kill_shell",
]);

const PATH_KEYS = new Set([
  "absolute_path",
  "cwd",
  "directory",
  "file_path",
  "filePaths",
  "notebook_path",
  "path",
  "paths",
  "target_directory",
]);

function expandHome(value) {
  if (value === "~") return homedir();
  if (value.startsWith("~/") || value.startsWith("~\\")) {
    return path.join(homedir(), value.slice(2));
  }
  return value;
}

function canonicalPath(value, cwd) {
  const absolute = path.resolve(cwd, expandHome(value));
  let existing = absolute;
  while (!existsSync(existing)) {
    const parent = path.dirname(existing);
    if (parent === existing) break;
    existing = parent;
  }

  try {
    const canonicalExisting = realpathSync.native(existing);
    return path.resolve(canonicalExisting, path.relative(existing, absolute));
  } catch {
    return absolute;
  }
}

function isWithinWorkspace(value, workspace) {
  if (typeof value !== "string" || value.length === 0) return true;
  const candidate = canonicalPath(value, workspace);
  const relative = path.relative(workspace, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function externalPath(input, workspace) {
  if (input === null || typeof input !== "object") return undefined;
  for (const [key, rawValue] of Object.entries(input)) {
    if (!PATH_KEYS.has(key)) continue;
    const values = Array.isArray(rawValue) ? rawValue : [rawValue];
    for (const value of values) {
      if (!isWithinWorkspace(value, workspace)) return value;
    }
  }
  return undefined;
}

export default function runnerPolicy(cmd) {
  cmd.addFlag("runner-permission", {
    type: "string",
    default: "safe-edit",
    description: "Permission level selected by the sub-agent runner.",
  });

  cmd.hooks({
    beforeToolCall: ({toolName, input}) => {
      const permission = cmd.getFlag("runner-permission");
      if (!PERMISSIONS.has(permission)) {
        return {
          block: true,
          additionalContext: `Unknown runner permission: ${String(permission)}`,
        };
      }
      if (permission === "yolo") return undefined;

      if (CONTROL_TOOLS.has(toolName)) {
        return {
          block: true,
          additionalContext: `${toolName} is disabled for ${permission} sub-agents.`,
        };
      }
      if (permission === "read-only" && READ_ONLY_BLOCKED_TOOLS.has(toolName)) {
        return {
          block: true,
          additionalContext: `${toolName} is disabled for read-only sub-agents.`,
        };
      }

      const outside = externalPath(input, canonicalPath(cmd.cwd, cmd.cwd));
      if (outside !== undefined) {
        return {
          block: true,
          additionalContext: `Access outside the working directory is disabled: ${outside}`,
        };
      }
      return undefined;
    },
  });
}
