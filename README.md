# Open Logix shared repo

Shared GitHub Actions and org-wide files for the [Open Logix](https://www.openlogix.org) repos.

This is an internal repo. It makes nothing for users. It holds pieces that the workflows of several Open Logix repos use, so each fix is made once.

## Actions

Composite actions in [actions/](actions), used from another repo's workflow like this:

```yaml
- name: Get the release and package tags
  uses: openlogix/.github/actions/get-release-and-package-tags@v1
  with:
    release-tag-var: RELEASE_TAG
    package-tag-var: PACKAGE_TAG
```

| Action | What it does |
| --- | --- |
| `get-release-and-package-tags` | Today's release tag `yyyy-mm-dd` and package tag `yyyymmdd`, into env vars |
| `ensure-no-conflicting-release` | Deletes a pre-release with the given tag, or fails if a stable release has it |
| `cleanup-old-prereleases` | Keeps the 5 newest pre-releases and deletes the rest with their tags |
| `create-pre-release` | Creates a pre-release with release notes and exactly the given files |
| `get-repo-info` | Repo `owner/repo`, branch and commit of a checked-out repo |
| `check-platform` | Fails unless the runner has the given OS and architecture |
| `scan-files-for-viruses` | Scans files, including inside archives, with ClamAV |
| `write-to-json-file` | Sets a string value in a JSON file |
| `format-json-file` | Formats a JSON file with aligned values |
| `set-env-from-json` | Copies JSON values into env vars |
| `json-to-text` | Turns a JSON file into aligned `key : value` lines |
| `assert-step-failed` | Fails unless an earlier step failed, for testing expected failures |

[actions-test.yaml](.github/workflows/actions-test.yaml) tests every action on each change and daily.

## Versions

Workflows use the `v1` tag. It moves forward when changes are tested, so fixes reach every repo without edits. A change that would break existing workflows gets a new major tag, `v2`.

## License

[Apache-2.0](LICENSE).
