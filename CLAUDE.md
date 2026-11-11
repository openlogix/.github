# CLAUDE.md

Guidance for Claude working in this repo, `openlogix/.github`. It is GitHub's special org-wide repo for Open Logix. Shared Open Logix rules (products, brand, repo naming) live in the website repo's `CLAUDE.md`, not here.

## What's here

- `actions/<name>/action.yaml`: composite GitHub Actions shared by the Open Logix repos' CI and release workflows. [README.md](README.md) lists them.
- `.github/workflows/actions-test.yaml`: tests the actions.
- `profile/README.md`: the org page at github.com/openlogix. A table of all Open Logix repos for developers: repo, what it is, status. The repo list, descriptions and statuses follow the website repo's `CLAUDE.md` (Repos, Availability); when those change, update this table too.
- Later, possibly: default community files (`CONTRIBUTING.md`, `SECURITY.md`, issue templates) and `workflow-templates/`. Add them only when asked.

Don't put product-owned things here, such as the engine's tool schemas or catalog. They stay in their product repo.

## Action rules

- Composite actions, `bash` unless noted. Every input has a description.
- Self-contained: an action never uses files from the calling repo's checkout unless an input names them. Actions that call `gh` set `GH_REPO: ${{ github.repository }}`, so they work in jobs with no checkout.
- Callers use `openlogix/.github/actions/<name>@main` (see Versions).

- **What goes here** (the website repo's `CLAUDE.md`, Shared CI): any workflow step or job that two Open Logix repos do the same way is an action or a reusable workflow here, never copied between repos. Actions are task-sized (a whole job's shared part), so a repo's workflow keeps only what's its own.

Action details:

- `start-pre-release`: the first job of a `build-pre-release` workflow, after checkout. `package-name`, `description`, `extra-fields` (optional JSON object, placed after the description). Today's tags (`yyyy-mm-dd`, `yyyymmdd`), fails if a stable release has that tag, writes `BUILD-INFO.json` (the standard fields: repo, branch, commit, tags, build time, workflow run) and uploads it as the `build-info` artifact; sets `RELEASE_TAG`, `PACKAGE_TAG`, `REPO`, `REPO_BRANCH`, `REPO_COMMIT` and outputs the two tags.
- `read-build-info`: the platform build jobs and the release job. Downloads `BUILD-INFO.json`, optionally adds `target-platform` (`platform`), sets the same five variables.
- `publish-pre-release`: the release job's end. `title`, `links`, `engine-note` (`catalog` or `core`: the "Use it through Logix Engine" note; `release_text.py` holds the one copy of both texts), `content-file` (the repo's own Markdown), `about-bullets`, `files` (the packages). Writes `SHA256SUMS` and the release text in the standard structure (the website repo's `CLAUDE.md`, Release text is user facing), deletes a same-tag pre-release and its tag and makes it again from scratch with the tag at the built commit (fails over a stable release), uploads the files with `BUILD-INFO.json` and `SHA256SUMS`, then deletes older pre-releases by the policy.
- `actions/release-policy.sh`: the pre-release policy, the one place it's set (`PRERELEASE_KEEP=5`: pre-releases are daily, so each is deleted after about 5 days unless made stable). `publish-pre-release` applies it and states it in every release text.
- `check-platform`: `os` is a substring of `uname -o`, `arch` equals `uname -m`, case-insensitive.
- `scan-files-for-viruses`: `file-patterns`; each pattern must match a file; ClamAV with `freshclam`, scans inside archives.
- `assert-step-failed`: `outcome`, `error-message`; for testing expected failures.
- `.github/workflows/publish-docs.yaml`: a reusable workflow (`workflow_call`) that builds a repo's MkDocs site with `--strict` and deploys it to its GitHub Pages. Callers keep the triggers, the Pages permissions and the `pages` concurrency group, and call `openlogix/.github/.github/workflows/publish-docs.yaml@main`.

## Tests

`actions-test.yaml` runs on every push and pull request, manually, and daily at 00:00 UTC. One job per action runs it and compares with expected output. Expected failures (a platform mismatch, an EICAR test file in the virus scan, publishing over a stable release) use `continue-on-error` plus `assert-step-failed`.

This repo has no releases of its own, so `test-publish-pre-release` tests the release action on test releases: the text's structure, the files, a same-tag replacement, stable-release protection and the cleanup. It works only on releases whose tags start with `test-`, runs one at a time, deletes its releases at the end, and deletes leftovers from earlier runs at the start. Add or update a test with every action change.

## Versions

- Callers use the latest actions: `openlogix/.github/actions/<name>@main` (and the docs workflow `@main`), with no version tags, for simplicity (as Apio does). A push to `main` here changes every caller's CI right away, so run `actions-test` first: on a branch, or check its result right after the push and fix forward.
- During initial development (no backward compatibility, the website repo's `CLAUDE.md`), a change that breaks callers updates every caller in the same go.

## Conventions

Use the current major versions of GitHub's actions (`actions/checkout`), and an exact version of any third-party action. Pushes to `main` reach every caller right away (see Versions).
