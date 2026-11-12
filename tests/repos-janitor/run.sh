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
out="$(python3 "$repo/repos-janitor/janitor.py" --state "$tmp/state.json" --summary "$tmp/summary.md" 2>&1)"
status=$?
failures=0
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }
[[ $status -eq 1 ]] || fail "expected exit 1, got $status"
if [[ "$out" != "$(cat "$here/expected-output.txt")" ]]; then
  fail "the findings differ:"; diff "$here/expected-output.txt" <(echo "$out")
fi
# The summary: counts, problems with fix links, and the passed items.
for text in \
  "# ❌ repos-janitor" \
  "**9 failures · 4 warnings · 29 passed**, in 8 repos (2026-11-14 03:00 UTC)." \
  "## ❌ Failures" \
  "## ⚠️ Warnings" \
  "## ✅ Passed" \
  "### logix-ide" \
  "<details><summary><b>logix-core</b>:" \
  "[the file](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc)" \
  "[the config, to repin](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc)" \
  "[the run](https://github.com/openlogix/logix-engine/actions/runs/1)" \
  "<br><sub>Why: Named by \`logix-ide\`'s \`001/dev.jsonc\`; installed products download it.</sub>" \
  "- **remote configs**: \`001/dev.jsonc\` is valid, and every release and file it names exists" \
  "- **remote configs**: \`001/channels.jsonc\` is valid" \
  "- **remote configs**: \`001/dev.jsonc\`: logix-catalog: no release 2026-11-30" \
  "- **schema copies**: \`docs/schemas/logix-board-v1.json\` equals \`logix-catalog\`'s \`schemas/logix-board-v1.json\`" \
  "- **latest release**: no release is marked Latest yet: no release is prod" \
  "- **latest release**: \`2026-11-01\` is marked Latest, a prod release" \
  "- **latest release**: \`2026-11-02\` is marked Latest, but it's on dev: Latest must be a prod release" \
  "- **latest release**: \`2026-11-05\` is marked Latest, but no recommended install uses a release of it" \
  "- **latest release**: \`2026-11-03\` is marked Latest, the newest release the recommended installs use" \
  "Used: \`2026-11-03\` by \`logix-engine\` \`2026-11-01\` (Latest) → \`logix-core\` \`2026-11-03\`." \
  "- **repo type**: installable (topic \`openlogix-installable\`)" \
  "- **repo type**: it has no type topic" \
  "- **repo type**: \`2026-11-01\` is a release, but the repo has none by its type"; do
  grep -qF -- "$text" "$tmp/summary.md" || fail "the summary has no \"$text\""
done
# The state now holds this run's SHA-256s.
if ! grep -q '"openlogix/logix-engine/2026-11-10/logix-engine-darwin-arm64-20261110.tgz": "[0-9a-f]\{64\}"' "$tmp/state.json" \
   || grep -q '"0000000000' "$tmp/state.json"; then
  fail "the state wasn't updated:"; cat "$tmp/state.json"
fi
[[ $failures -eq 0 ]] || exit 1
echo "All repos-janitor tests passed."
