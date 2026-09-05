#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
BuildAnchor installer

Usage:
  ./scripts/install.sh --local [--global]
  ./scripts/install.sh [--global]

Options:
  --local   Install the BuildAnchor checkout that contains this script.
  --global  Install the command for the current user, outside the active virtualenv.
  --help    Show this help.
EOF
}

local_install=false
global_install=false
explicit_global=false

for argument in "$@"; do
  case "$argument" in
    --local) local_install=true ;;
    --global) global_install=true; explicit_global=true ;;
    --help|-h) show_help; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; show_help >&2; exit 2 ;;
  esac
done

# When invoked without arguments (e.g., piped from curl), default to global user installation
if ! $local_install && ! $explicit_global; then
  global_install=true
fi

repository_url="${BUILDANCHOR_SOURCE_URL:-https://github.com/tensilestream/buildanchor/archive/refs/heads/main.tar.gz}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_dir}/.." && pwd)"
is_local_checkout=false

if [[ -f "${repository_root}/pyproject.toml" && -d "${repository_root}/src/buildanchor" ]]; then
  is_local_checkout=true
fi

if $local_install && ! $is_local_checkout; then
  echo "--local must be run from a BuildAnchor checkout." >&2
  exit 1
fi

if $local_install || $is_local_checkout; then
  source_path="${repository_root}"
  editable_args=(--editable)
else
  source_path="${repository_url}"
  editable_args=()
fi

if $global_install; then
  if command -v pipx >/dev/null 2>&1; then
    pipx install --force "${editable_args[@]}" "${source_path}"
    pipx ensurepath >/dev/null 2>&1 || true
  elif command -v python3 >/dev/null 2>&1; then
    if ! python3 -c 'import sys; raise SystemExit(sys.prefix != sys.base_prefix)' >/dev/null 2>&1; then
      echo "An active Python virtual environment is not a global install target. Install pipx or deactivate it and rerun this script." >&2
      exit 1
    fi
    user_base="$(python3 -m site --user-base 2>/dev/null || echo "${HOME}/.local")"
    if [[ -e "${user_base}" && ! -w "${user_base}" ]]; then
      echo "Error: User directory '${user_base}' exists but is not writable." >&2
      echo "If it is owned by root, restore permissions using:" >&2
      echo "  sudo chown -R \$(id -u):\$(id -g) \"${user_base}\"" >&2
      exit 1
    fi
    python3 -m pip install --user --upgrade "${editable_args[@]}" "${source_path}"
  else
    echo "BuildAnchor requires pipx or Python 3.10+." >&2
    exit 1
  fi
else
  if command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --upgrade "${editable_args[@]}" "${source_path}"
  else
    echo "BuildAnchor requires Python 3.10+." >&2
    exit 1
  fi
fi

echo "BuildAnchor installed. Run: buildanchor --help"
