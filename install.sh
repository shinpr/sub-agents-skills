#!/bin/bash
set -e

# sub-agents-skills installer
# Usage: curl -fsSL https://raw.githubusercontent.com/shinpr/sub-agents-skills/main/install.sh | bash -s -- --target <path>

TARGET=""
SKILL_NAME=""
REPO_URL="https://github.com/shinpr/sub-agents-skills"

usage() {
    echo "Usage: $0 --target <install-path> [--skill <skill-name>]"
    echo ""
    echo "Options:"
    echo "  --target <path>   Required. Directory to install skills into."
    echo "                    Examples:"
    echo "                      ~/.claude/skills      (Claude Code)"
    echo "                      ~/.cursor/skills      (Cursor)"
    echo "                      .github/skills        (VS Code/Copilot project)"
    echo ""
    echo "  --skill <name>    Optional. Install specific skill only."
    echo "                    Default: install all skills."
    echo ""
    echo "Examples:"
    echo "  $0 --target ~/.claude/skills"
    echo "  $0 --target ~/.cursor/skills --skill sub-agents"
    echo ""
    echo "  # Via curl:"
    echo "  curl -fsSL $REPO_URL/raw/main/install.sh | bash -s -- --target ~/.claude/skills"
    exit 1
}

error() {
    echo "Error: $1" >&2
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --target)
            TARGET="$2"
            shift 2
            ;;
        --skill)
            SKILL_NAME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

# Validate target
if [[ -z "$TARGET" ]]; then
    echo "Error: --target is required." >&2
    echo ""
    usage
fi

# Expand ~ to home directory
TARGET="${TARGET/#\~/$HOME}"

# Determine source directory
# If running from curl, we need to clone/download first
# If running locally, use the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SOURCE="$SCRIPT_DIR/skills"

# Check if running from curl (no local skills directory)
if [[ ! -d "$SKILLS_SOURCE" ]]; then
    echo "Downloading skills from repository..."
    TEMP_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_DIR" EXIT

    git clone --depth 1 "$REPO_URL" "$TEMP_DIR" 2>/dev/null || \
        error "Failed to clone repository. Please check the URL: $REPO_URL"

    SKILLS_SOURCE="$TEMP_DIR/skills"
fi

# Verify skills source exists
if [[ ! -d "$SKILLS_SOURCE" ]]; then
    error "Skills directory not found: $SKILLS_SOURCE"
fi

# Create target directory
mkdir -p "$TARGET"

# Install skills
installed=0

if [[ -n "$SKILL_NAME" ]]; then
    # Install specific skill
    if [[ ! -d "$SKILLS_SOURCE/$SKILL_NAME" ]]; then
        error "Skill not found: $SKILL_NAME"
    fi

    echo "Installing skill: $SKILL_NAME -> $TARGET/$SKILL_NAME"
    rm -rf "$TARGET/$SKILL_NAME"
    cp -r "$SKILLS_SOURCE/$SKILL_NAME" "$TARGET/"
    installed=1
else
    # Install all skills
    for skill_dir in "$SKILLS_SOURCE"/*/; do
        if [[ -d "$skill_dir" ]]; then
            skill_name=$(basename "$skill_dir")
            echo "Installing skill: $skill_name -> $TARGET/$skill_name"
            rm -rf "$TARGET/$skill_name"
            cp -r "${skill_dir%/}" "$TARGET/"
            ((installed++))
        fi
    done
fi

if [[ $installed -eq 0 ]]; then
    error "No skills found to install"
fi

echo ""
echo "Done! Installed $installed skill(s) to $TARGET"
