#!/bin/bash
# Tests repos-janitor/janitor.py on the fixture org in org/, with a fake gh
# that answers from it (fake-gh/gh) and repo pages from org/pages/. Each check has a case there that fires;
# the printed findings must be exactly expected-output.txt, and the summary
# page must list what passed and link where to fix each problem. Run from
# anywhere; needs python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PATH="$here/fake-gh:$PATH" FAKE_GH_ORG="$here/org" JANITOR_PAGES="$here/org/pages" JANITOR_NOW="2026-11-14T03:00:00Z"
out="$(python3 "$repo/repos-janitor/janitor.py" --summary "$tmp/summary.md" --results "$tmp/results.json" 2>&1)"
status=$?
failures=0
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }
[[ $status -eq 1 ]] || fail "expected exit 1, got $status"
if [[ "$out" != "$(cat "$here/expected-output.txt")" ]]; then
  fail "the findings differ:"; diff "$here/expected-output.txt" <(echo "$out")
fi
# The summary: counts, problems with fix links, and the passed items.
for text in \
  "❌&nbsp;&nbsp; **29 failed, 76 passed** · 8 repos · 2026-11-14 03:00 UTC" \
  "### Failed" \
  "### Passed" \
  "### Verification results" \
  "**openlogix/logix-ide**" \
  "<details><summary><b>openlogix/logix-core</b> · " \
  "- Release should be valid" \
  "    - [Why] Named by logix-ide [\`001/dev.jsonc\`](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc)." \
  "    - [Result] No \`BUILD-INFO.json\`." \
  "    - [Fix] [release](https://github.com/openlogix/logix-catalog/releases/tag/2026-11-01)" \
  "    - [Fix] [workflow](https://github.com/openlogix/logix-engine/actions/workflows/test.yaml) · [run](https://github.com/openlogix/logix-engine/actions/runs/1)" \
  "    - [Result] Failure (2026-11-14 00:00 UTC)." \
  "- Latest should be a prod release" \
  "    - [Result] On dev, not prod." \
  "    - [Result] Prod." \
  "    - [Result] No prod release yet." \
  "- Release should be Latest" \
  "    - [Why] Used by a recommended install: logix-engine [\`2026-11-01\`](https://github.com/openlogix/logix-engine/releases/tag/2026-11-01) (Latest) → [\`001/prod.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/prod.jsonc) → logix-core \`2026-11-03\`." \
  "    - [Why] On the prod list of [\`001/channels.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/channels.jsonc)." \
  "- Latest should be used by a recommended install" \
  "    - [Result] Not used by a recommended install." \
  "- Dev config should not lag behind prod" \
  "    - [Result] Older than prod's \`2026-11-05\`." \
  "    - [Fix] [dev config](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev.jsonc) · [prod config](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/prod.jsonc)" \
  "- Daily build should be recent" \
  "    - [Result] Last run 2 day(s) ago (2026-11-12 00:00 UTC)." \
  "- Repo metadata should be valid" \
  "    - [Why] A repo of the openlogix org." \
  "    - [Result] Type \`installable\`." \
  "    - [Result] Missing." \
  "    - [Fix] [create it](https://github.com/openlogix/openlogix.github.io/new/main?filename=.github/logix-repo-metadata.jsonc)" \
  "- Repo should have no releases" \
  "    - [Result] Has releases: \`2026-11-01\`." \
  "- Release tag should be a date" \
  "    - [Result] Not date-tagged: \`test-1-stable\`, \`test-1-p1\`." \
  "    - [Result] All date-tagged." \
  "- Schema copy should match its owner" \
  "    - [Result] Differs." \
  "- Repo settings should match" \
  "    - [Result] Mismatch: wiki should be off." \
  "    - [Result] Mismatch: private vulnerability reporting should be on." \
  "    - [Result] Mismatch: its README should link \`openlogix/logix-ide\`'s issues." \
  "    - [Fix] [settings](https://github.com/openlogix/logix-engine/settings)" \
  "- Repo sidebar should match its type" \
  "    - [Result] Mismatch: Releases should be hidden." \
  "    - [Fix] [repo page (the About gear)](https://github.com/openlogix/logix-cli)" \
  "- About should be set" \
  "    - [Result] Mismatch: its website should be https://www.openlogix.org/studio/." \
  "- Repo should have its standard files" \
  "    - [Result] Missing: \`LICENSE-APACHE\`." \
  "- README header should be standard" \
  "    - [Result] Standard." \
  "- README badges should match the workflows" \
  "    - [Result] No workflows." \
  "- Org lists should include the repo" \
  "    - [Result] Mismatch: not in the org page's repos table." \
  "    - [Result] Mismatch: \`test.yaml\` is labeled \`engine-test\` in the website Status, \`test\` in its README."; do
  grep -qF -- "$text" "$tmp/summary.md" || fail "the summary has no \"$text\""
done
# No warnings: every failure is an error.
! grep -q "arning" "$tmp/summary.md" || fail "the summary mentions warnings"
# The JSON results, as Apio's: by repo, then requirement type.
python3 - "$tmp/results.json" <<'PYEOF' || fail "the JSON results are wrong"
import json, sys
r = json.load(open(sys.argv[1]))
assert r["passed"] is False and set(r) == {"passed", "failures", "successes"}, list(r)
e = r["failures"]["openlogix/logix-catalog"]["release-should-be-valid"][0]
assert e["release_tag"] == "2026-11-01" and e["notes"] == [
    "[Why] Named by logix-ide 001/dev.jsonc.", "[Result] No BUILD-INFO.json."], e
assert "dev-should-not-lag-behind-prod" in r["failures"]["openlogix/logix-ide"]
assert "latest-should-be-prod" in r["successes"]["openlogix/logix-engine"]
PYEOF
[[ $failures -eq 0 ]] || exit 1
echo "All repos-janitor tests passed."
