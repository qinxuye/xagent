#!/bin/sh
# Xagent installer — https://get.xagent.co
#
#   curl -fsSL https://get.xagent.co | sh
#
# Installs the `xagent-ai` package (backend + bundled web UI) as an isolated uv
# tool, so nothing touches your system Python and PEP 668 never bites. On
# success the `xagent` command is available; start it and open the browser.
#
# Options (environment variables):
#   XAGENT_VERSION   pin a specific version, e.g. XAGENT_VERSION=0.6.0
#
# Prefer not to pipe curl into sh? The equivalent manual install is:
#   uv tool install xagent-ai        # or, in a venv: pip install xagent-ai
set -eu

APP="xagent-ai"
CMD="xagent"

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
err() {
  printf '\033[1;31merror:\033[0m %s\n' "$1" >&2
  exit 1
}

# uv supports Linux and macOS. Windows users should use pip in a venv.
os="$(uname -s)"
case "$os" in
  Linux | Darwin) ;;
  *) err "Unsupported OS '$os'. On Windows, install with: pip install $APP (in a virtualenv)." ;;
esac

# Ensure uv is available (isolates the install; avoids system-Python/PEP 668).
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv (Python tool manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs into ~/.local/bin (or ~/.cargo/bin on older installers); make it
  # visible to the rest of this script without requiring a new shell.
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    if [ -d "$d" ]; then
      case ":$PATH:" in
        *":$d:"*) ;;
        *) PATH="$d:$PATH" ;;
      esac
    fi
  done
  export PATH
fi
command -v uv >/dev/null 2>&1 || err "uv not found on PATH after install; open a new shell and re-run."

spec="$APP"
if [ -n "${XAGENT_VERSION:-}" ]; then
  spec="$APP==$XAGENT_VERSION"
fi

info "Installing $spec ..."
uv tool install --upgrade "$spec"

printf '\n'
info "Installed. Next steps:"
printf '\n'
printf '  Start Xagent:   %s\n' "$CMD"
printf '  Open:           http://127.0.0.1:8000\n'
printf '  Configure an LLM key (e.g. OPENAI_API_KEY) via a .env file or env var.\n'
printf '\n'

if ! command -v "$CMD" >/dev/null 2>&1; then
  warn "'$CMD' is not on your PATH in this shell yet."
  warn "Run 'uv tool update-shell' and open a new terminal, then run '$CMD'."
fi
