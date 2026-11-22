"""Checks a repo's remote configs: remote-config/<product>/ holds index.jsonc,
listing its config version folders, and a folder per config version,
<NNN>/, holding assign.jsonc and a config file per channel,
<channel>-channel.jsonc (Remote updates, in the website repo's CLAUDE.md).

Per product: index.jsonc against its schema (logix-config-index-v1),
listing exactly the version folders there. Per version folder: assign.jsonc
against its schema (logix-assign-v1), each release on at most one channel,
and every release on the prod list a stable release of this repo (--repo);
prod-channel.jsonc when there's a prod release, else at least
prod-channel.jsonc or dev-channel.jsonc; no config file for a channel that
isn't prod, dev or a key of assign.jsonc. Per config file: its JSONC
syntax, the schema (logix-remote-config-v1), and for each dependency: its
repo's owner (when --owner is given), that its release exists (stable, in
prod-channel.jsonc; never a draft), that its file exists for every
platform, and that the release's SHA256SUMS lists it. Its latest, the
component's own newest release on the channel: a release of this repo, the
same checks (its file when it names one), and, for a channel with a list in
assign.jsonc, the newest release on that list; required in
prod-channel.jsonc when there are prod releases. Every problem is reported,
as a GitHub annotation, and the exit status is 1 if there were any.

Releases are read with the gh CLI: `gh api repos/<repo>/releases/tags/<tag>`
and `gh release download <tag> -R <repo> -p SHA256SUMS -O -`.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema

VERSION = re.compile(r"^[0-9]{3,}$")
PRODUCT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CHANNEL_FILE = re.compile(r"^([a-z][a-z0-9-]*)-channel\.jsonc$")
ASSIGN = "assign.jsonc"
INDEX = "index.jsonc"


def strip_comments(text: str) -> str:
    """JSONC to JSON: removes // and /* */ comments outside strings."""
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
        elif c == '"':
            in_string = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("a /* comment isn't closed")
            # Keep the line breaks, so error line numbers stay right.
            out.append("\n" * text.count("\n", i, end))
            i = end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def gh(*args: str) -> tuple[int, str]:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    return r.returncode, r.stdout if r.returncode == 0 else r.stderr


NEWEST = "newest"
DATE_TAG = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def newest_release(repo: str) -> tuple[str | None, str]:
    """The tag `newest` stands for: the repo's newest date-tagged release,
    stable or pre-release, not drafts (the Remote updates rule in the website
    repo's CLAUDE.md). Returns (tag, "") or (None, the error)."""
    status, out = gh("api", f"repos/{repo}/releases?per_page=30")
    if status != 0:
        return None, f"can't list {repo}'s releases ({out.strip()})"
    tags = [r["tag_name"] for r in json.loads(out) if not r.get("draft") and DATE_TAG.match(r["tag_name"])]
    return (max(tags), "") if tags else (None, f"{repo} has no releases")


def check_dependency(
    name: str, dep: dict, platforms: list[str], owner: str, require_stable: bool
) -> list[str]:
    repo, tag, template = dep["repo"], dep["release"], dep.get("file")
    errors = []
    if owner and repo.split("/")[0] != owner:
        errors.append(f"{name}: {repo} isn't in {owner} (only allowed off main)")
    if tag == NEWEST:
        if require_stable:
            return errors + [f"{name}: a prod config names a stable release, never newest"]
        tag, problem = newest_release(repo)
        if tag is None:
            return errors + [f"{name}: newest: {problem}"]
    status, out = gh("api", f"repos/{repo}/releases/tags/{tag}")
    if status != 0:
        return errors + [f"{name}: no release {tag} in {repo} ({out.strip()})"]
    release = json.loads(out)
    if release.get("draft"):
        errors.append(f"{name}: {repo} {tag} is a draft")
    if require_stable and release.get("prerelease"):
        errors.append(f"{name}: {repo} {tag} is a pre-release; a prod config names only stable releases")
    if not template:
        return errors  # A latest without a file: nothing to download.
    assets = {a["name"] for a in release.get("assets", [])}
    files = sorted(
        {
            template.replace("${PLATFORM}", p).replace("${YYYYMMDD}", tag.replace("-", ""))
            for p in platforms
        }
    )
    for f in files:
        if f not in assets:
            errors.append(f"{name}: {repo} {tag} has no file {f}")
    if "SHA256SUMS" not in assets:
        return errors + [f"{name}: {repo} {tag} has no SHA256SUMS"]
    status, sums = gh("release", "download", tag, "-R", repo, "-p", "SHA256SUMS", "-O", "-")
    if status != 0:
        return errors + [f"{name}: can't read {repo} {tag} SHA256SUMS ({sums.strip()})"]
    listed = {line.split()[-1].lstrip("*") for line in sums.splitlines() if line.strip()}
    for f in files:
        if f in assets and f not in listed:
            errors.append(f"{name}: {repo} {tag} SHA256SUMS doesn't list {f}")
    return errors


def read_jsonc(path: Path):
    """A JSONC file's value, or the error text."""
    try:
        return json.loads(strip_comments(path.read_text(encoding="utf-8"))), None
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"not valid JSONC: {e}"


def schema_errors(value, schema: dict) -> list[str]:
    problems = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path)
    )
    return [f"{'/'.join(str(p) for p in e.path) or '(top)'}: {e.message}" for e in problems]


def check_file(
    path: Path, schema: dict, platforms: list[str], owner: str, channel: str,
    assigned: dict[str, list[str]], repo: str,
) -> list[str]:
    config, error = read_jsonc(path)
    if error:
        return [error]
    errors = schema_errors(config, schema)
    if errors:
        return errors
    prod = channel == "prod"
    for name, dep in config["dependencies"].items():
        errors += check_dependency(name, dep, platforms, owner, prod)
    latest = config.get("latest")
    listed = assigned.get(channel, [])
    if latest is None:
        if prod and listed:
            errors.append("latest is required: there are prod releases")
        return errors
    if latest["repo"] != repo:
        errors.append(f"latest: {latest['repo']} isn't this repo, {repo}")
        return errors
    errors += check_dependency("latest", latest, platforms, owner, prod)
    if listed and latest["release"] != max(listed):
        errors.append(f"latest: {latest['release']} isn't the newest {channel} release in {ASSIGN}, {max(listed)}")
    elif not listed and channel != "dev":
        errors.append(f"latest: {channel} has no releases in {ASSIGN}")
    elif channel == "dev":
        on = next((c for c, tags in assigned.items() if latest["release"] in tags), None)
        if on:
            errors.append(f"latest: {latest['release']} is on {on} in {ASSIGN}, not dev")
    return errors


def check_assign(path: Path, schema: dict, repo: str) -> tuple[dict, list[str]]:
    """assign.jsonc: its schema, each release on at most one channel, and
    every prod release a stable release of `repo`. Returns its channels."""
    channels, error = read_jsonc(path)
    if error:
        return {}, [error]
    errors = schema_errors(channels, schema)
    if errors:
        return {}, errors
    channels = {k: v for k, v in channels.items() if k != "$schema"}
    seen: dict[str, str] = {}
    for channel, tags in channels.items():
        for tag in tags:
            if tag in seen:
                errors.append(f"{tag} is on more than one channel ({seen[tag]}, {channel})")
            seen[tag] = channel
    for tag in channels.get("prod", []):
        status, out = gh("api", f"repos/{repo}/releases/tags/{tag}")
        if status != 0:
            errors.append(f"prod: no release {tag} in {repo} ({out.strip()})")
        elif json.loads(out).get("prerelease") or json.loads(out).get("draft"):
            errors.append(f"prod: {repo} {tag} isn't stable; a prod release must be")
    return channels, errors


def check_index(path: Path, schema: dict, versions: list[str]) -> list[str]:
    """index.jsonc: its schema, and that it lists exactly the version
    folders there, oldest first."""
    index, error = read_jsonc(path)
    if error:
        return [error]
    errors = schema_errors(index, schema)
    if errors:
        return errors
    listed = index["versions"]
    have = sorted(versions, key=int)
    if listed != have:
        errors.append(f"versions {listed} should be the version folders, oldest first: {have}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", required=True, help="the remote-config folder")
    ap.add_argument("--schemas", required=True, help="the folder with logix-remote-config-v1.json, logix-assign-v1.json and logix-config-index-v1.json")
    ap.add_argument("--repo", required=True, help="this repo, owner/name, whose releases assign.jsonc and latest name")
    ap.add_argument("--platforms", required=True, help="space-separated, such as darwin-arm64")
    ap.add_argument("--owner", default="", help="the only owner repos may have; empty for any")
    args = ap.parse_args()
    folder = Path(args.folder)
    schemas = Path(args.schemas)

    def schema(name: str) -> dict:
        return json.loads((schemas / name).read_text(encoding="utf-8"))

    config_schema = schema("logix-remote-config-v1.json")
    assign_schema = schema("logix-assign-v1.json")
    index_schema = schema("logix-config-index-v1.json")
    platforms = args.platforms.split()
    if not platforms:
        print("::error::no platforms given")
        return 1
    if not folder.is_dir():
        print(f"::error::no folder {folder}")
        return 1

    failed = False

    def report(where: Path, message: str) -> None:
        nonlocal failed
        failed = True
        print(f"::error file={where}::{message}")

    versions = []
    for product in sorted(folder.iterdir()):
        if not product.is_dir() or not PRODUCT.match(product.name):
            report(product, f"{product.name}: expected only product folders, such as logix-ide")
            continue
        found = []
        for v in sorted(product.iterdir()):
            if v.name == INDEX and v.is_file():
                continue
            if v.is_dir() and VERSION.match(v.name):
                found.append(v)
            else:
                report(v, f"{v.name}: expected only {INDEX} and config version folders, such as 001")
        versions += found
        index = product / INDEX
        if not index.is_file():
            report(index, f"no {INDEX}")
        else:
            errors = check_index(index, index_schema, [v.name for v in found])
            for e in errors:
                report(index, e)
            if not errors:
                print(f"{index}: ok")
    if not versions:
        report(folder, "no config version folders")
    for v in versions:
        assign_file = v / ASSIGN
        if not assign_file.is_file():
            report(assign_file, f"no {ASSIGN}")
            assigned = {}
        else:
            assigned, errors = check_assign(assign_file, assign_schema, args.repo)
            for e in errors:
                report(assign_file, e)
            if not errors:
                print(f"{assign_file}: ok")
        configs = {}
        for f in sorted(v.iterdir()):
            if f.name == ASSIGN:
                continue
            m = CHANNEL_FILE.match(f.name) if f.is_file() else None
            if not m:
                report(f, f"{f.name}: expected only {ASSIGN} and <channel>-channel.jsonc files")
            elif m.group(1) not in ("prod", "dev") and m.group(1) not in assigned:
                report(f, f"{f.name}: {m.group(1)} isn't a channel: add it to {ASSIGN}, or remove the file")
            else:
                configs[m.group(1)] = f
        if assigned.get("prod") and "prod" not in configs:
            report(v, "prod-channel.jsonc is required: there are prod releases")
        if not configs:
            report(v, "needs prod-channel.jsonc, or dev-channel.jsonc until the first promotion")
        for channel, f in configs.items():
            errors = check_file(f, config_schema, platforms, args.owner, channel, assigned, args.repo)
            for e in errors:
                report(f, e)
            if not errors:
                print(f"{f}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
