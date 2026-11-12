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
out="$(python3 "$repo/repos-janitor/janitor.py" --state "$tmp/state.json" --summary "$tmp/summary.md" --results "$tmp/results.json" 2>&1)"
status=$?
failures=0
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }
[[ $status -eq 1 ]] || fail "expected exit 1, got $status"
if [[ "$out" != "$(cat "$here/expected-output.txt")" ]]; then
  fail "the findings differ:"; diff "$here/expected-output.txt" <(echo "$out")
fi
# The summary: counts, problems with fix links, and the passed items.
for text in \
  "❌&nbsp;&nbsp; **10 errors, 4 warnings, 33 passed** · 8 repos · 2026-11-14 03:00 UTC" \
  "### Errors" \
  "### Warnings" \
  "### Passed" \
  "### Verification results" \
  "**openlogix/logix-ide**" \
  "<details><summary><b>openlogix/logix-core</b> · " \
  "- Release should be valid" \
  "    - [Why] Named by logix-ide [\`001/dev.jsonc\`](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc)." \
  "    - [Result] It has no \`BUILD-INFO.json\`." \
  "    - [Fix] [the release](https://github.com/openlogix/logix-catalog/releases/tag/2026-11-01)" \
  "    - [Fix] [the config, to repin](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc) · [the newest release](https://github.com/openlogix/logix-catalog/releases/tag/2026-11-12)" \
  "    - [Fix] [the workflow](https://github.com/openlogix/logix-engine/actions/workflows/test.yaml) · [the run](https://github.com/openlogix/logix-engine/actions/runs/1)" \
  "- Latest should be a prod release" \
  "    - [Result] It's on dev: promote it, or mark a prod release Latest." \
  "    - [Result] It's on the prod list of its version's \`channels.jsonc\`." \
  "    - [Result] No release is prod yet, so none can be Latest." \
  "- Release should be Latest" \
  "    - [Why] Used by a recommended install: logix-engine [\`2026-11-01\`](https://github.com/openlogix/logix-engine/releases/tag/2026-11-01) (Latest) → [\`001/prod.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/prod.jsonc) → logix-core \`2026-11-03\`." \
  "    - [Why] On the prod list of [\`001/channels.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/channels.jsonc): a prod release must be stable." \
  "- Latest should be used by a recommended install" \
  "- Dev pin should be recent" \
  "- Repo should have one type" \
  "- Repo should have no releases"; do
  grep -qF -- "$text" "$tmp/summary.md" || fail "the summary has no \"$text\""
done
# The JSON results, as Apio's: by repo, then requirement type.
python3 - "$tmp/results.json" <<'PYEOF' || fail "the JSON results are wrong"
import json, sys
r = json.load(open(sys.argv[1]))
assert r["passed"] is False
e = r["errors"]["openlogix/logix-catalog"]["release-should-be-valid"][0]
assert e["release_tag"] == "2026-11-01" and e["notes"] == [
    "[Why] Named by logix-ide 001/dev.jsonc.", "[Result] It has no BUILD-INFO.json."], e
assert "pin-should-be-recent" in r["warnings"]["openlogix/logix-ide"]
assert "latest-should-be-prod" in r["successes"]["openlogix/logix-engine"]
PYEOF
# The state now holds this run's SHA-256s.
if ! grep -q '"openlogix/logix-engine/2026-11-10/logix-engine-darwin-arm64-20261110.tgz": "[0-9a-f]\{64\}"' "$tmp/state.json" \
   || grep -q '"0000000000' "$tmp/state.json"; then
  fail "the state wasn't updated:"; cat "$tmp/state.json"
fi
[[ $failures -eq 0 ]] || exit 1
echo "All repos-janitor tests passed."
