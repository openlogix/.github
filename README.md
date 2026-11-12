<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://www.openlogix.org/brand/readme/shared-dark.svg">
  <img alt="Open Logix shared repo" src="https://www.openlogix.org/brand/readme/shared-light.svg" height="96">
</picture>

<br>

[![License: MIT or Apache 2.0](https://img.shields.io/badge/License-MIT%20or%20Apache%202.0-blue)](#license)

[![actions-test](https://img.shields.io/github/actions/workflow/status/openlogix/.github/actions-test.yaml?branch=main&event=schedule&label=actions-test)](https://github.com/openlogix/.github/actions/workflows/actions-test.yaml)
[![repos-janitor](https://img.shields.io/github/actions/workflow/status/openlogix/.github/repos-janitor.yaml?branch=main&label=repos-janitor)](https://github.com/openlogix/.github/actions/workflows/repos-janitor.yaml)

Shared GitHub Actions and org-wide files for the [Open Logix](https://www.openlogix.org) repos.

This is an internal repo. It makes nothing for users. It holds pieces that the workflows of several Open Logix repos use, so each fix is made once.

## Org page

[profile/README.md](profile/README.md) is shown on [github.com/openlogix](https://github.com/openlogix). It lists the Open Logix repos.

## Actions

Composite actions in [actions/](actions), and one reusable workflow, used from the other repos' workflows. A release workflow, for example:

```yaml
- uses: openlogix/.github/actions/start-pre-release@main      # first job
  with:
    package-name: logix-catalog
    description: Logix Catalog's boards and FPGAs, as Logix Engine downloads them.
- uses: openlogix/.github/actions/publish-pre-release@main    # last job
  with:
    title: "Logix Catalog: Every FPGA board, described once."
    engine-note: catalog
    files: _out/logix-catalog-${{ env.PACKAGE_TAG }}.tgz
```

| Action | What it does |
| --- | --- |
| `start-pre-release` | A release workflow's start: today's tags, the stable-release check, `BUILD-INFO.json`, also shown on the run's summary page |
| `read-build-info` | Gets `BUILD-INFO.json` in a later job, with the platform, into env vars |
| `publish-pre-release` | A release workflow's end: checksums, the standard release text, the day's pre-release made again from scratch, and the cleanup |
| `check-platform` | Fails unless the runner has the given OS and architecture |
| `scan-files-for-viruses` | Scans files, including inside archives, with ClamAV |
| `check-remote-config` | Checks a repo's remote configs (`remote-config/<product>/<NNN>/`: `channels.jsonc` and a config file per channel): the schemas, the channels, and that every named release exists, has its files, and is stable in `prod.jsonc` |
| `assert-step-failed` | Fails unless an earlier step failed, for testing expected failures |
| [`publish-docs.yaml`](.github/workflows/publish-docs.yaml) (reusable workflow) | Builds a repo's MkDocs site and deploys it to its GitHub Pages |

The pre-release policy (pre-releases are kept about 5 days unless made stable) is set in one place, [`actions/release-policy.sh`](actions/release-policy.sh).

[actions-test.yaml](.github/workflows/actions-test.yaml) tests every action on each change and daily.

## repos-janitor

[repos-janitor.yaml](.github/workflows/repos-janitor.yaml) checks across the Open Logix repos every 6 hours what no single repo's CI can see, as requirements (as Apio's janitor): remote configs valid, the releases they name valid and (for prod) stable, each repo's Latest right for its type, dev configs not behind prod, release tags that are dates, the website's schema copies, every repo's daily builds, and each repo's type in its `.github/logix-repo-metadata.jsonc`. Its run summary lists what failed, each with why it applies, the result and links to fix it, then what passed; the run, and its badge above, go red on any failure. It only reads, and only reports for now.

## Versions

Workflows use the latest version, `@main`, so a fix reaches every repo as soon as it's pushed. Changes are tested by [actions-test.yaml](.github/workflows/actions-test.yaml).

## License

[MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option.
