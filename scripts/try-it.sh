#!/usr/bin/env bash
# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0
#
# Evaluate BuildAnchor in about a minute, on your own repositories.
#
#   ./scripts/try-it.sh                    # this repository
#   ./scripts/try-it.sh ~/code/*           # several at once
#   ./scripts/try-it.sh --verify ~/code/api
#
# Nothing is written and nothing is executed unless you pass --verify. Without
# it, the script shows what BuildAnchor *would* run and stops there.

set -uo pipefail

VERIFY=0
PATHS=()

for arg in "$@"; do
  case "$arg" in
    --verify) VERIFY=1 ;;
    -h|--help) sed -n '4,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) PATHS+=("$arg") ;;
  esac
done
[ ${#PATHS[@]} -eq 0 ] && PATHS=(".")

# Prefer a checkout's own virtualenv, then an installed buildanchor, then uvx.
HERE="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$HERE/.venv/bin/buildanchor" ]; then
  BA="$HERE/.venv/bin/buildanchor"
elif command -v buildanchor >/dev/null 2>&1; then
  BA="buildanchor"
elif command -v uvx >/dev/null 2>&1; then
  BA="uvx buildanchor"
else
  echo "Install first:  pip install buildanchor    (or: uvx buildanchor doctor)" >&2
  exit 1
fi

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
dim()  { printf '\033[2m%s\033[0m\n' "$1"; }

bold "BuildAnchor — what does it know about your repositories?"
dim  "Reading only. Nothing is executed or written without --verify."
echo

for path in "${PATHS[@]}"; do
  [ -d "$path" ] || continue
  name="$(basename "$(cd "$path" && pwd)")"
  echo "──────────────────────────────────────────────────────────────"
  bold "$name"
  echo "  $(cd "$path" && pwd)"
  echo

  # One call answers shape, commands, declared runners and anything wrong.
  $BA doctor --workspace "$path" 2>&1 | sed 's/^/  /'
  echo

  # What verification would execute, without executing it.
  dim "  If you ran 'buildanchor verify', it would execute:"
  $BA verify --dry-run --workspace "$path" 2>&1 | sed -n '3,12p' | sed 's/^/  /'
  echo

  if [ "$VERIFY" -eq 1 ]; then
    bold "  Verifying (this runs a discovery probe — no test bodies)"
    $BA verify --workspace "$path" 2>&1 | sed 's/^/  /'
    echo
  fi
done

echo "──────────────────────────────────────────────────────────────"
bold "What to check"
cat <<'EOF'
  1. Is the test command right for each repository? Compare it with what you
     actually type. If it is wrong, that is the bug worth reporting — and
     'buildanchor doctor <path>' will tell you which rule produced it.

  2. Re-run with --verify to prove the commands run. A probe loads your test
     files without running any test body; it takes seconds.

  3. If a project is missing from a monorepo listing, ask why:
        buildanchor doctor path/to/that/project

  4. Nothing here wrote to your repository. When you want it to:
        buildanchor init --dry-run     # exactly what it would write
        buildanchor init               # write it
        buildanchor init --undo        # remove it, byte-for-byte
EOF
