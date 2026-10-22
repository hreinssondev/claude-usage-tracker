#!/usr/bin/env bash
# claude-usage-tracker installer.
#   ./install.sh              install or update, and turn on
#   ./install.sh --off        turn off (keeps files and history)
#   ./install.sh --on         turn back on
#   ./install.sh --uninstall  turn off and remove (history is saved to your home folder first)
set -euo pipefail

CONFIG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$CONFIG/usage-tracker"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARK="# claude-usage-tracker"

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }
case "$(uname -s)" in Darwin|Linux) ;; *) echo "Only macOS and Linux are supported." >&2; exit 1 ;; esac

rc_file() {
  case "$(basename "${SHELL:-}")" in
    zsh) echo "$HOME/.zshrc" ;;
    bash) [ "$(uname -s)" = Darwin ] && echo "$HOME/.bash_profile" || echo "$HOME/.bashrc" ;;
    *) echo "" ;;
  esac
}

remove_alias() {
  local rc="$1"
  [ -f "$rc" ] && grep -q "$MARK" "$rc" || return 0
  grep -v "$MARK" "$rc" > "$rc.tmp-usage-tracker" || true
  cat "$rc.tmp-usage-tracker" > "$rc" && rm "$rc.tmp-usage-tracker"
}

add_alias() {
  local rc; rc="$(rc_file)"
  if [ -z "$rc" ]; then
    echo "Add this to your shell config for the report command:"
    echo "  alias cc-usage='python3 \"$DEST/tracker.py\"'"
    return
  fi
  remove_alias "$rc"
  echo "alias cc-usage='python3 \"$DEST/tracker.py\"'  $MARK" >> "$rc"
  echo "Added the cc-usage command to $rc (open a new terminal to use it)."
}

case "${1:-}" in
  "")
    mkdir -p "$DEST"
    cp "$SRC/tracker.py" "$DEST/tracker.py"
    python3 "$DEST/tracker.py" on
    add_alias
    ;;
  --on)  python3 "$DEST/tracker.py" on ;;
  --off) python3 "$DEST/tracker.py" off ;;
  --uninstall)
    [ -f "$DEST/tracker.py" ] && python3 "$DEST/tracker.py" off
    if [ -s "$DEST/ledger.jsonl" ]; then
      backup="$HOME/claude-usage-history-$(date +%Y%m%d-%H%M%S).jsonl"
      cp "$DEST/ledger.jsonl" "$backup"
      echo "Saved your answer history to $backup"
    fi
    rm -rf "$DEST"
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do remove_alias "$rc"; done
    echo "Uninstalled."
    ;;
  *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
