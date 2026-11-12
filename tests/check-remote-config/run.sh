#!/bin/bash
# Tests check_remote_config.py on the cases in cases/, with a fake gh that
# answers from the fixture releases in releases/. Run from anywhere; needs
# python3 with jsonschema. Used by actions-test.yaml.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/../.."
export PATH="$here/fake-gh:$PATH" FAKE_GH_RELEASES="$here/releases"
failures=0

# expect <case> <owner> <0 or 1> [<text the output must contain>]; with
# STABLE=1 set, pre-releases are refused.
expect() {
  local out status
  out="$(python3 "$repo/actions/check-remote-config/check_remote_config.py" \
    --folder "$here/cases/$1/remote-config" \
    --schema "$repo/schemas/logix-remote-config-v1.json" \
    --platforms "darwin-arm64 linux-x86-64" --owner "$2" \
    ${STABLE:+--require-stable} 2>&1)"
  status=$?
  if [[ $status -ne $3 || ( -n "${4:-}" && "$out" != *"$4"* ) ]]; then
    echo "FAIL: $1 (owner '$2'): expected exit $3${4:+ and \"$4\"}, got $status:"
    echo "$out"
    failures=$((failures + 1))
  else
    echo "ok: $1 (owner '$2')"
  fi
}

expect good openlogix 0 "config-001.jsonc: ok"
expect prerelease openlogix 0 "config-001.jsonc: ok"
STABLE=1 expect prerelease openlogix 1 "is a pre-release"
expect no-release openlogix 1 "no release 2026-11-30 in openlogix/logix-catalog"
expect no-file openlogix 1 "has no file logix-catalog-linux-x86-64-20261124.tgz"
expect not-in-sums openlogix 1 "SHA256SUMS doesn't list logix-catalog-20261126.tgz"
expect fork openlogix 1 "someone/logix-catalog isn't in openlogix"
expect fork "" 0
expect bad-schema openlogix 1 "logix-catalog/release: '2026/11/24' does not match"
expect bad-schema openlogix 1 "logix-catalog/file: '../evil.tgz' does not match"
expect bad-name openlogix 1 "expected only config-NNN.jsonc files"
expect bad-jsonc openlogix 1 "not valid JSONC"

if [[ $failures -ne 0 ]]; then
  echo "$failures failed"
  exit 1
fi
echo "All check-remote-config tests passed."
