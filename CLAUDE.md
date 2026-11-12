# CLAUDE.md

Guidance for Claude working in this repo, `openlogix/.github`. It is GitHub's special org-wide repo for Open Logix. Shared Open Logix rules (products, brand, repo naming) live in the website repo's `CLAUDE.md`, not here.

## What's here

- `actions/<name>/action.yaml`: composite GitHub Actions shared by the Open Logix repos' CI and release workflows. [README.md](README.md) lists them.
- `.github/workflows/actions-test.yaml`: tests the actions.
- `profile/README.md`: the org page at github.com/openlogix. A table of all Open Logix repos for developers: repo, what it is, status. The repo list, descriptions and statuses follow the website repo's `CLAUDE.md` (Repos, Availability); when those change, update this table too.
- `schemas/logix-remote-config-v1.json`: the schema every remote config names in `$schema` (Remote updates, the website repo's `CLAUDE.md`), owned here; the website serves a copy at `www.openlogix.org/schemas/`, so copy it there whenever it changes. Until the first release it changes without a new version.
- `tests/<action>/`: fixtures for an action's tests, such as `tests/check-remote-config/` (cases, fixture releases, a fake `gh` and `run.sh`).
- Later, possibly: default community files (`CONTRIBUTING.md`, `SECURITY.md`, issue templates) and `workflow-templates/`. Add them only when asked.

Don't put product-owned things here, such as the engine's tool schemas or catalog. They stay in their product repo.

## repos-janitor (built 2026-11-12)

A workflow here that checks what no single repo's CI can see and reports problems in one place, modeled on Apio's janitor. The user's decisions (from the proposal doc, now obsolete):

- **Name:** `repos-janitor`, also on its badge ("check" undersold it: it will also fix later). Files: `.github/workflows/repos-janitor.yaml` and `repos-janitor/janitor.py` (Python, the gh CLI with the run's own token; `PLATFORMS`, the platforms configs must serve, gets Windows and Linux with Milestones 3 and 4; the thresholds are constants at its top; the pre-release count comes from `actions/release-policy.sh`). It skips its own workflow in check 6. The state for check 4 is `janitor-state.json`, restored from and saved to the Actions cache (a new key per run, saved even when the run fails). A full run takes about a minute.
- **When:** every 6 hours, at 03:00, 09:00, 15:00 and 21:00 UTC (as Apio's; the 03:00 run is three hours after the 00:00 UTC builds and tests start), and manually. It must stay quick (a few minutes): API reads only, no checkouts of other repos beyond the files it reads. Not after each repo's build (deferred indefinitely, the user's decision 2026-11-12): a `repository_dispatch` from the product repos would need a token that can write to this repo stored in each of them, giving every product repo's developers write access to the org repo. **Access rule** (the user's, 2026-11-12): this repo may get access to the product repos (such as a token or GitHub App for fixes later), never the other way around.
- **Which repos:** every public repo in the `openlogix` org, found through the API; each check applies to the repos that have what it checks (a `remote-config/` folder, scheduled workflows, releases), so a new repo needs no list edit. Read-only, with the workflow's own token (the repos are public).
- **Checks** (start with these seven; keep, drop or add later; a candidate to add: the org page's table and the website README's Status section list every repo and workflow). A failure is something broken now; a warning, something that breaks soon or needs a person:
  1. Remote configs on `main`: each repo's config files pass `check-remote-config` (runs that action's script, so there's one definition of a valid config). Fails: any doesn't.
  2. Expiring pre-releases: a config pinned to a pre-release the policy is about to delete. Fails: it's gone. Warns: 2 days before it expires (among the 2 oldest kept). Keeping more than 5 daily pre-releases (`actions/release-policy.sh`) would give more time to repin.
  3. Stale pins: warns when a named release is more than 7 days older than the dependency's newest.
  4. Rebuilt releases: warns when a named file's SHA-256 differs from the last run's (installed products download it again); the last run's hashes are kept as a workflow artifact or cache.
  5. Schema copies: fails when a file in the website's `docs/schemas/` differs from its owner's (`logix-catalog`'s `schemas/`, this repo's `schemas/`).
  6. Daily runs: fails when a workflow's latest scheduled run on `main` failed; warns when it didn't run in the last 26 hours.
  7. Releases (date-tagged ones only; the test releases `actions-test` makes here are skipped): fails when a day's pre-release lacks `SHA256SUMS` or `BUILD-INFO.json`, or lists a file it doesn't have.
- **Report** (as Apio's: what passed too, and links to fix): the run's summary page has the counts, then three sections, Failures, Warnings and Passed, each with a table per repo (check, item, why, links); the why says why the check applies to that item, as Apio's do (such as "named by `openlogix/logix-ide`'s `remote-config/logix-ide/config-001.jsonc`"); names, tags, files and paths are in code format. Each problem links to where it's fixed: the config to repin, the release page, where a pre-release can be made stable, the failed run and its workflow, the owner's file and the copy. The log has one GitHub annotation per problem (plain text, with its first link) and a count line. The run fails on any failure, so its badge turns red; the badge is in the website README's Status section as `repos-janitor`. That's all for now: a GitHub issue here that it opens, updates and closes (GitHub emails the watchers), and email to developers (needs an SMTP account and a mail action) come after the public release. GitHub already emails a scheduled run's failure to whoever last changed its schedule.
- **Tests:** `tests/repos-janitor/run.sh`: a fake `gh` (`fake-gh/gh`, Python) answering from a fixture org (`org/repos/openlogix/<repo>/`: `files/`, `releases.json`, `sums/<tag>`, `runs/<workflow>.json`) where each check fires once, and a state file with a changed SHA-256; the printed findings must be exactly `expected-output.txt` (failures, then warnings, by repo and check), and the summary must have the counts, fix links and passed items. Run by `actions-test.yaml` (`test-repos-janitor`).
- **Not now: fixes**, including auto-repin (when check 2 warns, a pull request moving the config to the newest pre-release, tested by that repo's CI and merged only after review; needs a write token, a GitHub App). Fixing comes after the reports have proved right for a while.

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
- `check-remote-config`: `folder` (default `remote-config`), `platforms` (what `${PLATFORM}` stands for, required), `owner` (default `openlogix`), `require-stable` (default `false`: pre-releases are allowed while Open Logix is in development). Needs a checkout. Checks every `<product>/config-NNN.jsonc` (`check_remote_config.py`, jsonschema in a venv): file names, JSONC, the schema in `schemas/`, and per dependency that its release exists (not a draft; stable, with `require-stable`), its file exists for each platform, and the release's `SHA256SUMS` lists it; on `main` and pull requests into `main`, repos must be in `owner`. Reports every problem as an annotation, then fails. Reads releases with `gh api repos/<repo>/releases/tags/<tag>` and `gh release download <tag> -R <repo> -p SHA256SUMS -O -` (the tests' fake `gh` answers exactly these). A component's own checks (its tests with the named releases, the dependencies its source expects) stay in its repo.
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
