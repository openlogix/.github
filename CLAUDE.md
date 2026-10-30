# CLAUDE.md

Guidance for Claude working in this repo, `openlogix/.github`. It is GitHub's special org-wide repo for Open Logix. Shared Open Logix rules (products, brand, repo naming) live in the website repo's `CLAUDE.md`, not here.

## What's here

- `actions/<name>/action.yaml`: composite GitHub Actions shared by the Open Logix repos' CI and release workflows. [README.md](README.md) lists them.
- `.github/workflows/actions-test.yaml`: tests the actions.
- Later, possibly: `profile/README.md` (shown on github.com/openlogix), default community files (`CONTRIBUTING.md`, `SECURITY.md`, issue templates) and `workflow-templates/`. Add them only when asked.

Don't put product-owned things here, such as the engine's tool schemas or catalog. They stay in their product repo.

## Action rules

- Composite actions, `bash` unless noted. Every input has a description.
- Self-contained: an action never uses files from the calling repo's checkout unless an input names them. Actions that call `gh` set `GH_REPO: ${{ github.repository }}`, so they work in jobs with no checkout.
- Callers use `openlogix/.github/actions/<name>@v1`.

Action details:

- `get-release-and-package-tags`: env var names in (`release-tag-var`, `package-tag-var`); today's UTC date as `yyyy-mm-dd` and `yyyymmdd`.
- `ensure-no-conflicting-release`: `release-tag`, `just-check`. Deletes a same-tag pre-release and its tag unless just checking; fails on a stable one; no release is fine.
- `cleanup-old-prereleases`: keeps the 5 newest non-draft pre-releases, deletes the others with their tags. Stable releases and drafts are untouched.
- `create-pre-release`: `release_tag`, `body_path`, `files`. Name = tag. Fails if the tag exists; a missing file fails the run.
- `get-repo-info`: `repo-dir`, optional `repo-var`/`branch-var`/`commit-var`. Repo is lowercase `owner/repo`; branch is `detached` for a detached HEAD; origin must be `https://github.com/owner/repo[.git]`.
- `check-platform`: `os` is a substring of `uname -o`, `arch` equals `uname -m`, case-insensitive.
- `write-to-json-file`: `json-file`, `key`, string `value`, with `jq`.
- `format-json-file`: aligned values with the `json-align` npm tool.
- `set-env-from-json`: `mappings`, "ENV_VAR key" pairs; a missing key fails.
- `json-to-text`: Python; aligned `key : value` lines, keys in file order.
- `scan-files-for-viruses`: `file-patterns`; each pattern must match a file; ClamAV with `freshclam`, scans inside archives.
- `assert-step-failed`: `outcome`, `error-message`; for testing expected failures.

## Tests

`actions-test.yaml` runs on every push and pull request, manually, and daily at 00:00 UTC. One job per action runs it and compares with expected output. Expected failures (a platform mismatch, an EICAR test file in the virus scan, creating an existing release, a stable release conflict) use `continue-on-error` plus `assert-step-failed`.

This repo has no releases of its own, so `test-release-actions` also tests the three actions that change releases. It works only on releases whose tags start with `test-`, runs one at a time, deletes its releases at the end, and deletes leftovers from earlier runs at the start. Add or update a test with every action change.

## Versions

- Callers pin the major tag `v1`. After a change passes `actions-test` on `main`, move the tag: `git tag -f v1 && git push -f origin v1`. Moving `v1` changes every caller's CI right away, so check with the user first.
- A change that breaks existing callers (a removed or renamed input, changed behavior) gets `v2`. Keep `v1` working until the callers move.

## Conventions

Use the current major versions of GitHub's actions (`actions/checkout`), and an exact version of third-party actions (`softprops/action-gh-release`). Commit freely; pushes to `main` affect only this repo's tests until `v1` moves.
