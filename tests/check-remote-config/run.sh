#!/bin/bash
# Tests check_remote_config.py on the cases in cases/, with a fake gh that
# answers from the fixture releases in releases/. Run from anywhere; needs
# python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
export PATH="$here/fake-gh:$PATH" FAKE_GH_RELEASES="$here/releases"
failures=0

# expect <case> <owner> <0 or 1> [<text the output must contain>]
expect() {
  local out status
  out="$(python3 "$repo/actions/check-remote-config/check_remote_config.py" \
    --folder "$here/cases/$1/remote-config" \
    --schemas "$repo/schemas" \
    --repo openlogix/logix-engine \
    --platforms "darwin-arm64 linux-x86-64" --owner "$2" 2>&1)"
  status=$?
  if [[ $status -ne $3 || ( -n "${4:-}" && "$out" != *"$4"* ) ]]; then
    echo "FAIL: $1 (owner '$2'): expected exit $3${4:+ and \"$4\"}, got $status:"
    echo "$out"
    failures=$((failures + 1))
  else
    echo "ok: $1 (owner '$2')"
  fi
}

expect good openlogix 0 "dev-channel.jsonc: ok"
expect good openlogix 0 "assign.jsonc: ok"
expect good openlogix 0 "index.jsonc: ok"
expect prerelease openlogix 0 "dev-channel.jsonc: ok"
expect no-release openlogix 1 "no release 2026-11-30 in openlogix/logix-catalog"
expect no-file openlogix 1 "has no file logix-catalog-linux-x86-64-20261124.tgz"
expect not-in-sums openlogix 1 "SHA256SUMS doesn't list logix-catalog-20261126.tgz"
expect fork openlogix 1 "someone/logix-catalog isn't in openlogix"
expect fork "" 0
expect bad-schema openlogix 1 "dependencies/logix-catalog/release: '2026/11/24' does not match"
expect bad-schema openlogix 1 "dependencies/logix-catalog/file: '../evil.tgz' does not match"
expect bad-jsonc openlogix 1 "not valid JSONC"
expect prod-good openlogix 0 "prod-channel.jsonc: ok"
expect prod-only openlogix 0 "prod-channel.jsonc: ok"
expect prod-only-prerelease openlogix 1 "is a pre-release; a prod config names only stable releases"
expect prod-prerelease openlogix 1 "is a pre-release; a prod config names only stable releases"
expect prod-not-stable openlogix 1 "prod: openlogix/logix-engine 2026-11-21 isn't stable"
expect prod-no-release openlogix 1 "prod: no release 2026-11-22 in openlogix/logix-engine"
expect prod-no-file openlogix 1 "prod-channel.jsonc is required: there are prod releases"
expect two-channels openlogix 1 "2026-11-20 is on more than one channel"
expect beta openlogix 0 "beta-channel.jsonc: ok"
expect unknown-channel openlogix 1 "beta isn't a channel"
expect no-config openlogix 1 "needs prod-channel.jsonc, or dev-channel.jsonc until the first promotion"
expect no-channels openlogix 1 "no assign.jsonc"
expect channels-dev openlogix 1 "does not match any of the regexes"
expect bad-version openlogix 1 "expected only index.jsonc and config version folders"
expect old-layout openlogix 1 "expected only index.jsonc and config version folders"
expect bad-file-name openlogix 1 "expected only assign.jsonc and <channel>-channel.jsonc files"
expect latest-missing openlogix 1 "latest is required: there are prod releases"
expect latest-old openlogix 1 "latest: 2026-11-19 isn't the newest prod release in assign.jsonc, 2026-11-20"
expect latest-other-repo openlogix 1 "latest: openlogix/logix-ide isn't this repo, openlogix/logix-engine"
expect dev-latest-on-prod openlogix 1 "latest: 2026-11-20 is on prod in assign.jsonc, not dev"
# newest: the repo's newest release, checked as that release; never on prod
# or in latest.
expect newest openlogix 0 "dev-channel.jsonc: ok"
expect newest-broken openlogix 1 "SHA256SUMS doesn't list logix-catalog-20261126.tgz"
expect prod-newest openlogix 1 "logix-core: a prod config names a stable release, never newest"
expect latest-newest openlogix 1 "latest/release: 'newest' does not match"
expect index-mismatch openlogix 1 "should be the version folders, oldest first"
expect no-index openlogix 1 "no index.jsonc"

if [[ $failures -ne 0 ]]; then
  echo "$failures failed"
  exit 1
fi
echo "All check-remote-config tests passed."
