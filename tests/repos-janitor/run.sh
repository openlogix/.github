#!/bin/bash
# Tests repos-janitor/janitor.py on the fixture org in org/, with a fake gh
# that answers from it (fake-gh/gh). Each check has a case there that fires;
# the output must be exactly the expected findings. Run from anywhere; needs
# python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp "$here/state.json" "$tmp/state.json"
export PATH="$here/fake-gh:$PATH" FAKE_GH_ORG="$here/org" JANITOR_NOW="2026-11-14T03:00:00Z"
out="$(python3 "$repo/repos-janitor/janitor.py" --state "$tmp/state.json" 2>&1)"
status=$?
expected='::error title=releases (openlogix/logix-catalog)::2026-11-01 has no BUILD-INFO.json
::error title=daily runs (openlogix/logix-engine)::test.yaml'"'"'s last scheduled run failure: https://github.com/openlogix/logix-engine/actions/runs/1
::error title=releases (openlogix/logix-engine)::2026-11-14'"'"'s SHA256SUMS lists logix-engine-linux-x86-64-20261114.tgz, which the release doesn'"'"'t have
::error title=remote configs (openlogix/logix-engine)::remote-config/logix-engine/config-001.jsonc: logix-catalog: no release 2026-11-30 in openlogix/logix-catalog (HTTP 404: Not Found)
::error title=schema copies (openlogix/openlogix.github.io)::docs/schemas/logix-other-v1.json has no owner (schemas/logix-other-v1.json in logix-catalog, .github)
::error title=schema copies (openlogix/openlogix.github.io)::docs/schemas/logix-remote-config-v1.json differs from openlogix/.github'"'"'s schemas/logix-remote-config-v1.json: copy it again
::warning title=daily runs (openlogix/logix-ide)::build.yaml hasn'"'"'t run since 2026-11-12T00:00:00Z
::warning title=expiring pre-releases (openlogix/logix-ide)::remote-config/logix-ide/config-001.jsonc: logix-engine 2026-11-10 is a pre-release deleted in about 1 day(s): repin it
::warning title=rebuilt releases (openlogix/logix-ide)::remote-config/logix-ide/config-001.jsonc: logix-engine 2026-11-10: logix-engine-darwin-arm64-20261110.tgz was rebuilt under the same tag (its SHA-256 changed), so installed products download it again
::warning title=stale pins (openlogix/logix-ide)::remote-config/logix-ide/config-001.jsonc: logix-catalog 2026-11-01 is 11 days older than openlogix/logix-catalog'"'"'s newest release, 2026-11-12'
failures=0
if [[ $status -ne 1 ]]; then echo "FAIL: expected exit 1, got $status"; failures=1; fi
if [[ "$out" != "$expected" ]]; then
  echo "FAIL: the findings differ:"; diff <(echo "$expected") <(echo "$out"); failures=1
fi
# The state now holds this run's SHA-256s.
if ! grep -q '"openlogix/logix-engine/2026-11-10/logix-engine-darwin-arm64-20261110.tgz": "[0-9a-f]\{64\}"' "$tmp/state.json" \
   || grep -q '"0000000000' "$tmp/state.json"; then
  echo "FAIL: the state wasn't updated:"; cat "$tmp/state.json"; failures=1
fi
if [[ $failures -ne 0 ]]; then exit 1; fi
echo "All repos-janitor tests passed."
