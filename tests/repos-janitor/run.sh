#!/bin/bash
# Tests repos-janitor/janitor.py on the fixture org in org/, with a fake gh
# that answers from it (fake-gh/gh) and repo pages from org/pages/ and web pages from org/web/. Each check has a case there that fires;
# the printed findings must be exactly expected-output.txt, and the summary
# page must list what passed and link where to fix each problem. Run from
# anywhere; needs python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PATH="$here/fake-gh:$PATH" FAKE_GH_ORG="$here/org" JANITOR_PAGES="$here/org/pages" JANITOR_WEB="$here/org/web" JANITOR_NOW="2026-11-14T03:00:00Z"
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
  "❌&nbsp;&nbsp; **31 failed, 83 passed** · 8 repos · 2026-11-14 03:00 UTC" \
  "### Failed" \
  "### Passed" \
  "### Verification results" \
  "**openlogix/logix-ide**" \
  "<details><summary><b>openlogix/logix-core</b> · " \
  "- Release should be valid" \
  "    - [Why] Named by logix-ide [\`001/dev-channel.jsonc\`](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev-channel.jsonc)." \
  "    - [Result] No \`BUILD-INFO.json\`." \
  "    - [Fix] [release](https://github.com/openlogix/logix-catalog/releases/tag/2026-11-01)" \
  "    - [Fix] [workflow](https://github.com/openlogix/logix-engine/actions/workflows/test.yaml) · [run](https://github.com/openlogix/logix-engine/actions/runs/1)" \
  "    - [Result] Failure (2026-11-14 00:00 UTC)." \
  "    - [Result] Passed (2026-11-12 00:05 UTC, push)." \
  "- Latest should be a prod release" \
  "    - [Result] On dev, not prod." \
  "    - [Result] Prod." \
  "    - [Result] No prod release yet." \
  "- Release should be Latest" \
  "    - [Why] Used by a recommended install: logix-engine [\`2026-11-01\`](https://github.com/openlogix/logix-engine/releases/tag/2026-11-01) (Latest) → [\`001/prod-channel.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/prod-channel.jsonc) → logix-core \`2026-11-03\`." \
  "    - [Why] On the prod list of [\`001/assign.jsonc\`](https://github.com/openlogix/logix-engine/blob/main/remote-config/logix-engine/001/assign.jsonc)." \
  "- Latest should be used by a recommended install" \
  "    - [Result] Not used by a recommended install." \
  "- Dev config should not lag behind prod" \
  "    - [Result] Older than prod's \`2026-11-05\`." \
  "    - [Fix] [dev config](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/dev-channel.jsonc) · [prod config](https://github.com/openlogix/logix-ide/blob/main/remote-config/logix-ide/001/prod-channel.jsonc)" \
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
  "- llms.txt and its links should resolve" \
  "    - [Why] Linked from \`llms.txt\`." \
  "    - [Result] No anchor \`#old-section\` on the page." \
  "    - [Result] Answers 404." \
  "    - [Fix] [llms.txt](https://github.com/openlogix/openlogix.github.io/blob/main/docs/llms.txt) · [its docs](https://github.com/openlogix/logix-ide/tree/main/docs)" \
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
    "[Why] Named by logix-ide 001/dev-channel.jsonc.", "[Result] No BUILD-INFO.json."], e
assert "dev-should-not-lag-behind-prod" in r["failures"]["openlogix/logix-ide"]
assert "latest-should-be-prod" in r["successes"]["openlogix/logix-engine"]
PYEOF
# The run that says whether a workflow passes (newest_result).
python3 - "$repo/repos-janitor" <<'PYEOF' || fail "newest_result picks the wrong run"
import sys, datetime as dt
sys.path.insert(0, sys.argv[1])
from janitor import newest_result
def run(n, event, conclusion, day, hour, status="completed"):
    return {"id": n, "event": event, "status": status, "conclusion": conclusion,
            "created_at": f"2026-11-{day:02}T{hour:02}:00:00Z"}
at = dt.datetime(2026, 11, 14, 12, tzinfo=dt.timezone.utc)
def pick(*runs):
    r, problem = newest_result(list(runs), at)
    return (r and r["id"], problem)
# A scheduled failure, then a push that passed: the push decides.
assert pick(run(1, "schedule", "failure", 14, 0), run(2, "push", "success", 14, 1)) == (2, None)
# Cancelled runs are skipped: the run before them decides.
assert pick(run(1, "push", "failure", 14, 0), run(2, "schedule", "cancelled", 14, 1)) == (1, None)
assert pick(run(1, "push", "success", 13, 0), run(2, "schedule", "cancelled", 14, 1)) == (1, None)
# Skipping back more than 36 hours is a failure, passing run or not.
assert pick(run(1, "push", "success", 12, 0), run(2, "schedule", "cancelled", 12, 23),
            run(3, "schedule", "cancelled", 13, 23)) == (1, "Only cancelled runs since 2026-11-12 23:00 UTC (over 36 hours).")
assert pick(run(1, "schedule", "cancelled", 12, 0))[1] is not None
# Old cancelled runs before a newer result don't matter.
assert pick(run(1, "schedule", "cancelled", 10, 0), run(2, "push", "success", 14, 0)) == (2, None)
# Runs still going and pull requests don't count.
assert pick(run(1, "schedule", "success", 14, 0), run(2, "push", None, 14, 1, status="in_progress"),
            run(3, "pull_request", "failure", 14, 2)) == (1, None)
assert pick() == (None, None)
PYEOF
[[ $failures -eq 0 ]] || exit 1
echo "All repos-janitor tests passed."
