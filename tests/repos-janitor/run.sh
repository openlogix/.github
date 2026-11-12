#!/bin/bash
# Tests repos-janitor/janitor.py on the fixture org in org/, with a fake gh
# that answers from it (fake-gh/gh). Each check has a case there that fires;
# the printed findings must be exactly expected-output.txt, and the summary
# page must list what passed and link where to fix each problem. Run from
# anywhere; needs python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp "$here/state.json" "$tmp/state.json"
export PATH="$here/fake-gh:$PATH" FAKE_GH_ORG="$here/org" JANITOR_NOW="2026-11-14T03:00:00Z"
export GITHUB_STEP_SUMMARY="$tmp/summary.md"
out="$(python3 "$repo/repos-janitor/janitor.py" --state "$tmp/state.json" 2>&1)"
status=$?
failures=0
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }
[[ $status -eq 1 ]] || fail "expected exit 1, got $status"
if [[ "$out" != "$(cat "$here/expected-output.txt")" ]]; then
  fail "the findings differ:"; diff "$here/expected-output.txt" <(echo "$out")
fi
# The summary: counts, problems with fix links, and the passed items.
for text in \
  "6 failures, 5 warnings, 7 passed." \
  "## Failures (6)" \
  "## Warnings (5)" \
  "## Passed (7)" \
  "| Named by \`openlogix/logix-ide\`'s \`remote-config/logix-ide/config-001.jsonc\`. |" \
  "### \`openlogix/logix-ide\`" \
  "[the release, to make it stable](https://github.com/openlogix/logix-engine/releases/tag/2026-11-10)" \
  "[the config, to repin](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/config-001.jsonc)" \
  "[the run](https://github.com/openlogix/logix-engine/actions/runs/1)" \
  "| latest release | \`2026-11-05\` is marked Latest, but the remote configs recommend \`2026-11-01\`" \
  "| latest release | no release is marked Latest yet" \
  "| remote configs | \`remote-config/logix-ide/config-001.jsonc\` is valid" \
  "| schema copies | \`docs/schemas/logix-board-v1.json\` equals \`openlogix/logix-catalog\`'s \`schemas/logix-board-v1.json\`"; do
  grep -qF -- "$text" "$tmp/summary.md" || fail "the summary has no \"$text\""
done
# The state now holds this run's SHA-256s.
if ! grep -q '"openlogix/logix-engine/2026-11-10/logix-engine-darwin-arm64-20261110.tgz": "[0-9a-f]\{64\}"' "$tmp/state.json" \
   || grep -q '"0000000000' "$tmp/state.json"; then
  fail "the state wasn't updated:"; cat "$tmp/state.json"
fi
[[ $failures -eq 0 ]] || exit 1
echo "All repos-janitor tests passed."
