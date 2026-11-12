# CLAUDE.md

Guidance for Claude working in this repo, `openlogix/.github`. It is GitHub's special org-wide repo for Open Logix. Shared Open Logix rules (products, brand, repo naming) live in the website repo's `CLAUDE.md`, not here.

## What's here

**Repo type: no releases** (`.github/logix-repo-metadata.jsonc`; the website repo's `CLAUDE.md`, Repo types, owns the policy). Its tests make no releases (the release tests use a fake `gh`), so it has none.

- `actions/<name>/action.yaml`: composite GitHub Actions shared by the Open Logix repos' CI and release workflows. [README.md](README.md) lists them.
- `.github/workflows/actions-test.yaml`: tests the actions.
- `profile/README.md`: the org page at github.com/openlogix. A table of all Open Logix repos for developers: repo, what it is, status. The repo list, descriptions and statuses follow the website repo's `CLAUDE.md` (Repos, Availability); when those change, update this table too.
- `schemas/logix-repo-metadata-v1.json`: the schema of every repo's `.github/logix-repo-metadata.jsonc` (its type; Repo types, in the website repo's `CLAUDE.md`), owned here, with a copy on the website.
- `schemas/logix-remote-config-v1.json`: the schema every remote config names in `$schema` (Remote updates, the website repo's `CLAUDE.md`), owned here; the website serves a copy at `www.openlogix.org/schemas/`, so copy it there whenever it changes. Until the first release it changes without a new version.
- `tests/<action>/`: fixtures for an action's tests, such as `tests/check-remote-config/` (cases, fixture releases, a fake `gh` and `run.sh`).
- Later, possibly: default community files (`CONTRIBUTING.md`, `SECURITY.md`, issue templates) and `workflow-templates/`. Add them only when asked.

Don't put product-owned things here, such as the engine's tool schemas or catalog. They stay in their product repo.

## repos-janitor (built 2026-11-12)

A workflow here that checks what no single repo's CI can see and reports problems in one place, modeled on Apio's janitor. The user's decisions (from the proposal doc, now obsolete):

- **Name:** `repos-janitor`, also on its badge ("check" undersold it: it will also fix later). Files: `.github/workflows/repos-janitor.yaml` and `repos-janitor/janitor.py` (Python, the gh CLI with the run's own token; `PLATFORMS`, the platforms configs must serve, gets Windows and Linux with Milestones 3 and 4; the run age limit is a constant at its top; the pre-release count comes from `actions/release-policy.sh`). It skips its own workflow in the daily-build checks. It keeps no state between runs: each run checks what's there now. A full run takes about a minute.
- **When:** every 6 hours, at 03:00, 09:00, 15:00 and 21:00 UTC (as Apio's; the 03:00 run is three hours after the 00:00 UTC builds and tests start), and manually. It must stay quick (a few minutes): API reads only, no checkouts of other repos beyond the files it reads. Not after each repo's build (deferred indefinitely, the user's decision 2026-11-12): a `repository_dispatch` from the product repos would need a token that can write to this repo stored in each of them, giving every product repo's developers write access to the org repo. **Access rule** (the user's, 2026-11-12): this repo may get access to the product repos (such as a token or GitHub App for fixes later), never the other way around.
- **Which repos:** every public repo in the `openlogix` org, found through the API; each check applies to the repos that have what it checks (a `remote-config/` folder, scheduled workflows, releases), so a new repo needs no list edit. Read-only, with the workflow's own token (the repos are public).
- **The model** (as Apio's janitor, the user's decision 2026-11-12): a *requirement type* has an id and a title, and every failure is an error (the user's rule, 2026-11-12: no warnings, so nothing is a judgment call: either it needs fixing or it passes; a check that can fail without anything to fix, or clears itself, doesn't belong, so a stable release file changed by hand isn't one); analyzers create *requirement instances*, a type applied to one subject (a repo, a release, a config file, a workflow), with why it applies (`[Why]` notes, one short reason each, such as "A repo of the openlogix org." or "Named by logix-ide `001/dev.jsonc`."; the rules and allowed values belong in the result or the docs, not the why: several analyzers add reasons to the same instance, such as a release named by two configs) and links to examine or fix it; verifiers add the result (`[Result]`) and pass or fail it. Later, fixers may act on failed instances (`[Fix]` links today). `janitor.py`'s `REQUIREMENTS` lists the types:

  | Requirement | Subject |
  | --- | --- |
  | `release-tag-should-be-a-date` | a repo's releases: every published release (not drafts) is tagged `yyyy-mm-dd`; the other checks see only those |
  | `config-should-be-valid` | a file of a version folder (`check-remote-config`'s problems by file) |
  | `release-should-be-valid` | a release named by a config, on a prod list, or Latest (`SHA256SUMS`, `BUILD-INFO.json`, the files `SHA256SUMS` lists) |
  | `release-should-be-stable` | a release on a prod list, or named by a `prod.jsonc` |
  | `release-should-be-latest` | a dependency's newest reachable release; an installable's newest prod release |
  | `latest-should-be-used` | a dependency's Latest no recommended install reaches |
  | `latest-should-be-prod` | an installable's Latest (or none: passes while no release is prod) |
  | `dev-should-not-lag-behind-prod` | a `dev.jsonc` entry naming an older release than its version's `prod.jsonc` (a dev file left over after a promotion: delete it, so dev uses prod's, or repin it) |
  | `schema-copy-should-match` | a website `docs/schemas/` file |
  | `daily-build-should-pass` | a scheduled workflow's last scheduled run on `main` |
  | `daily-build-should-be-recent` | the same, run in the last 26 hours |
  | `repo-metadata-should-be-valid` | a repo's `.github/logix-repo-metadata.jsonc`: present, valid against `schemas/logix-repo-metadata-v1.json` (`type`: `installable`, `dependency`, `no-releases`) |
  | `repo-should-have-no-releases` | a "no releases" repo |

  The rules behind them are the website repo's `CLAUDE.md` (Remote updates, Repo types).
- **Report** (as Apio's, the user's format 2026-11-12): `janitor-summary.md`, added to the run's summary page by its own step (seeing it needs a signed-in browser): a status line (`❌&nbsp;&nbsp; **N errors, N passed** · N repos · time`), then `### Errors`, by repo (`**owner/repo**`), then requirement title, then subject (linked), each with `[Why]` lines, a `[Result]` line and a `[Fix]` line of links (the config to repin, the release, where a pre-release is made stable, the run and workflow, the schema files, the repo to set its topic); `### Passed`, the same per repo, folded (`<details>`, `[See]` links); `### Verification results`, the JSON, folded. `janitor-results.json` has the same results for tools, as Apio's: `passed`, then `failures` and `successes`, each by repo and requirement id, an entry per subject (its fields, such as `release_tag`, `subject`, `notes` as `[Why] …`/`[Result] …` plain text, `links`). Both files are uploaded as the `repos-janitor-report` artifact, also when a requirement failed. The log has one GitHub annotation per failed instance and a count line. The run fails on any failure, so its badge turns red; the badge is in the website README's Status section as `repos-janitor`. That's all for now: a GitHub issue here that it opens, updates and closes (GitHub emails the watchers), and email to developers (needs an SMTP account and a mail action) come after the public release. GitHub already emails a scheduled run's failure to whoever last changed its schedule.
- **Tests:** `tests/repos-janitor/run.sh`: a fake `gh` (`fake-gh/gh`, Python) answering from a fixture org (`org/repos/openlogix/<repo>/`: `topics`, `files/`, `releases.json`, `sums/<tag>`, `files-of/<tag>/<name>` such as `BUILD-INFO.json`, `runs/<workflow>.json`) where each check fires once; the printed findings must be exactly `expected-output.txt` (failures, then warnings, by repo and check), and the summary must have the counts, fix links and passed items. Run by `actions-test.yaml` (`test-repos-janitor`).
- **Not now: fixes**, including auto-repin (when a dev pin falls behind, `pin-should-be-recent`, a pull request moving the config to the newest release, tested by that repo's CI and merged only after review; needs a write token, a GitHub App). Fixing comes after the reports have proved right for a while.

## Action rules

- Composite actions, `bash` unless noted. Every input has a description.
- Self-contained: an action never uses files from the calling repo's checkout unless an input names them. Actions that call `gh` set `GH_REPO: ${{ github.repository }}`, so they work in jobs with no checkout.
- Callers use `openlogix/.github/actions/<name>@main` (see Versions).

- **What goes here** (the website repo's `CLAUDE.md`, Shared CI): any workflow step or job that two Open Logix repos do the same way is an action or a reusable workflow here, never copied between repos. Actions are task-sized (a whole job's shared part), so a repo's workflow keeps only what's its own.

Action details:

- `start-pre-release`: the first job of a `build-pre-release` workflow, after checkout. `package-name`, `description`, `extra-fields` (optional JSON object, placed after the description). Today's tags (`yyyy-mm-dd`, `yyyymmdd`), fails if a stable release has that tag, writes `BUILD-INFO.json` (the standard fields: repo, branch, commit, tags, build time, workflow run) and uploads it as the `build-info` artifact, and shows it on the run's summary page as a table of fields (also kept in `$RUNNER_TEMP/build-info.md`, which the test reads, since each step has its own summary file) (values in code format, the commit linked; the website repo's `CLAUDE.md`, Shared CI, makes that a rule for every build workflow); sets `RELEASE_TAG`, `PACKAGE_TAG`, `REPO`, `REPO_BRANCH`, `REPO_COMMIT` and outputs the two tags.
- `read-build-info`: the platform build jobs and the release job. Downloads `BUILD-INFO.json`, optionally adds `target-platform` (`platform`), sets the same five variables.
- `publish-pre-release`: the release job's end. `title`, `links`, `engine-note` (`catalog` or `core`: the "Use it through Logix Engine" note; `release_text.py` holds the one copy of both texts), `content-file` (the repo's own Markdown), `about-bullets`, `files` (the packages). Writes `SHA256SUMS` and the release text in the standard structure (the website repo's `CLAUDE.md`, Release text is user facing), deletes a same-tag pre-release and its tag and makes it again from scratch with the tag at the built commit (fails over a stable release), uploads the files with `BUILD-INFO.json` and `SHA256SUMS`, then deletes older pre-releases by the policy.
- `actions/release-policy.sh`: the pre-release policy, the one place it's set (`PRERELEASE_KEEP=5`). `publish-pre-release` applies it after publishing: it keeps the newest `PRERELEASE_KEEP` pre-releases and any pre-release a remote config on `main` names, in any public Open Logix repo, any config version and channel (`actions/publish-pre-release/referenced_tags.py`, read-only, through `gh api`; a failure to read a config stops the cleanup, so nothing is deleted on partial information), and deletes the rest; stable releases are never deleted (Remote updates, Release cleanup, in the website repo's `CLAUDE.md`). Each repo's build deletes only its own repo's releases, with its own token; `repos-janitor` never deletes. `release_text.py` states the policy in every release text. Tested by `tests/referenced-tags/run.sh` (on the `repos-janitor` fixture org).
- `check-platform`: `os` is a substring of `uname -o`, `arch` equals `uname -m`, case-insensitive.
- `scan-files-for-viruses`: `file-patterns`; each pattern must match a file; ClamAV with `freshclam`, scans inside archives.
- `check-remote-config`: `folder` (default `remote-config`), `platforms` (what `${PLATFORM}` stands for, required), `owner` (default `openlogix`). Needs a checkout. Checks every version folder `<product>/<NNN>/` (`check_remote_config.py`, jsonschema in a venv; the layout and rules are the website repo's `CLAUDE.md`, Remote updates): only `channels.jsonc` and `<channel>.jsonc` files there; `channels.jsonc` against `schemas/logix-channels-v1.json` (no `dev` key, tags only), each release on at most one channel, every `prod` release an existing stable release of this repo (`--repo`, the workflow's repo); a config file only for `prod`, `dev` or a channel of `channels.jsonc`; `prod.jsonc` when there are prod releases, else at least `prod.jsonc` or `dev.jsonc`. Each config file: JSONC, `schemas/logix-remote-config-v1.json`, and per dependency that its release exists (not a draft; stable in `prod.jsonc`), its file exists for each platform, and the release's `SHA256SUMS` lists it; on `main` and pull requests into `main`, repos must be in `owner`. Reports every problem as an annotation, then fails. Reads releases with `gh api repos/<repo>/releases/tags/<tag>` and `gh release download <tag> -R <repo> -p SHA256SUMS -O -` (the tests' fake `gh` answers exactly these). A component's own checks (its tests with the named releases, the dependencies its source expects) stay in its repo.
- `assert-step-failed`: `outcome`, `error-message`; for testing expected failures.
- `.github/workflows/publish-docs.yaml`: a reusable workflow (`workflow_call`) that builds a repo's MkDocs site with `--strict` and deploys it to its GitHub Pages. Callers keep the triggers, the Pages permissions and the `pages` concurrency group, and call `openlogix/.github/.github/workflows/publish-docs.yaml@main`.

## Tests

`actions-test.yaml` runs on every push and pull request, manually, and daily at 00:00 UTC. One job per action runs it and compares with expected output. Expected failures (a platform mismatch, an EICAR test file in the virus scan, publishing over a stable release) use `continue-on-error` plus `assert-step-failed`.

`test-publish-pre-release` tests the release action without making real releases (the user's decision, 2026-11-12: this repo's type is "no releases"): `tests/publish-pre-release/fake-gh/gh`, a stateful stand-in for `gh` that keeps the releases in a JSON file (`$FAKE_GH_STATE`; `release view`, `create`, `delete` and `list`, with `--json` and `--jq` through the real `jq`; `gh api` goes to the `repos-janitor` fake, on `tests/publish-pre-release/org`, where logix-ide's `dev.jsonc` names the test release `p1`). It checks the text's structure, the files, the target commit, a same-tag replacement, stable-release protection and the cleanup (the newest 5 kept, plus `p1`, which a config names; stable releases and drafts left). The real `gh` calls are exercised every day by the product repos' builds, and `repos-janitor` checks their releases. Add or update a test with every action change.

## Versions

- Callers use the latest actions: `openlogix/.github/actions/<name>@main` (and the docs workflow `@main`), with no version tags, for simplicity (as Apio does). A push to `main` here changes every caller's CI right away, so run `actions-test` first: on a branch, or check its result right after the push and fix forward.
- During initial development (no backward compatibility, the website repo's `CLAUDE.md`), a change that breaks callers updates every caller in the same go.

## Conventions

Use the current major versions of GitHub's actions (`actions/checkout`), and an exact version of any third-party action. Pushes to `main` reach every caller right away (see Versions).
