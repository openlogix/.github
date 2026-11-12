#!/bin/bash
# Tests actions/publish-pre-release/referenced_tags.py on the repos-janitor
# fixture org (its fake gh answers the same calls): the release tags of a
# repo that some remote config there names. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
export PATH="$repo/tests/repos-janitor/fake-gh:$PATH" FAKE_GH_ORG="$repo/tests/repos-janitor/org"
failures=0
check() {
  local out
  out="$(python3 "$repo/actions/publish-pre-release/referenced_tags.py" "$1" 2>&1 | tr '\n' ' ')"
  if [[ "$out" != "$2" ]]; then
    echo "FAIL: $1: expected \"$2\", got \"$out\""; failures=$((failures + 1))
  else
    echo "ok: $1"
  fi
}
# logix-ide's 001/dev.jsonc names engine 2026-11-10, its prod.jsonc
# 2026-11-01; nothing names the IDE.
check openlogix/logix-engine "2026-11-01 2026-11-10 "
# Named by logix-ide's dev.jsonc and prod.jsonc, logix-engine's dev.jsonc and
# logix-studio's.
check openlogix/logix-catalog "2026-11-01 2026-11-05 2026-11-12 2026-11-30 "
check openlogix/logix-core "2026-11-03 "
check openlogix/logix-ide ""
[[ $failures -eq 0 ]] || exit 1
echo "All referenced-tags tests passed."
