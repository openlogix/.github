"""Checks a repo's remote configs: remote-config/<product>/<NNN>/, a folder
per config version, holding channels.jsonc and a config file per channel,
<channel>.jsonc (Remote updates, in the website repo's CLAUDE.md).

Per version folder: channels.jsonc against its schema (logix-channels-v1),
each release on at most one channel, and every release on the prod list a
stable release of this repo (--repo); prod.jsonc when there's a prod
release, else at least prod.jsonc or dev.jsonc; no config file for a
channel that isn't prod, dev or a key of channels.jsonc. Per config file:
its JSONC syntax, the schema (logix-remote-config-v1), and for each
dependency: its repo's owner (when --owner is given), that its release
exists (stable, in prod.jsonc; never a draft), that its file exists for
every platform, and that the release's SHA256SUMS lists it. Every problem
is reported, as a GitHub annotation, and the exit status is 1 if there
were any.

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

VERSION = re.compile(r"^[0-9]{3}$")
PRODUCT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CHANNEL_FILE = re.compile(r"^([a-z][a-z0-9-]*)\.jsonc$")
CHANNELS = "channels.jsonc"


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


def check_dependency(
    name: str, dep: dict, platforms: list[str], owner: str, require_stable: bool
) -> list[str]:
    repo, tag, template = dep["repo"], dep["release"], dep["file"]
    errors = []
    if owner and repo.split("/")[0] != owner:
        errors.append(f"{name}: {repo} isn't in {owner} (only allowed off main)")
    status, out = gh("api", f"repos/{repo}/releases/tags/{tag}")
    if status != 0:
        return errors + [f"{name}: no release {tag} in {repo} ({out.strip()})"]
    release = json.loads(out)
    if release.get("draft"):
        errors.append(f"{name}: {repo} {tag} is a draft")
    if require_stable and release.get("prerelease"):
        errors.append(f"{name}: {repo} {tag} is a pre-release; a prod config names only stable releases")
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
    path: Path, schema: dict, platforms: list[str], owner: str, require_stable: bool
) -> list[str]:
    config, error = read_jsonc(path)
    if error:
        return [error]
    errors = schema_errors(config, schema)
    if errors:
        return errors
    for name, dep in config.items():
        if name != "$schema":
            errors += check_dependency(name, dep, platforms, owner, require_stable)
    return errors


def check_channels(path: Path, schema: dict, repo: str) -> tuple[dict, list[str]]:
    """channels.jsonc: its schema, each release on at most one channel, and
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", required=True, help="the remote-config folder")
    ap.add_argument("--schemas", required=True, help="the folder with logix-remote-config-v1.json and logix-channels-v1.json")
    ap.add_argument("--repo", required=True, help="this repo, owner/name, whose releases the channels list")
    ap.add_argument("--platforms", required=True, help="space-separated, such as darwin-arm64")
    ap.add_argument("--owner", default="", help="the only owner repos may have; empty for any")
    args = ap.parse_args()
    folder = Path(args.folder)
    schemas = Path(args.schemas)
    config_schema = json.loads((schemas / "logix-remote-config-v1.json").read_text(encoding="utf-8"))
    channels_schema = json.loads((schemas / "logix-channels-v1.json").read_text(encoding="utf-8"))
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
        for v in sorted(product.iterdir()):
            if v.is_dir() and VERSION.match(v.name):
                versions.append(v)
            else:
                report(v, f"{v.name}: expected only config version folders, such as 001")
    if not versions:
        report(folder, "no config version folders")
    for v in versions:
        channels_file = v / CHANNELS
        if not channels_file.is_file():
            report(channels_file, f"no {CHANNELS}")
            channels = {}
        else:
            channels, errors = check_channels(channels_file, channels_schema, args.repo)
            for e in errors:
                report(channels_file, e)
            if not errors:
                print(f"{channels_file}: ok")
        configs = {}
        for f in sorted(v.iterdir()):
            if f.name == CHANNELS:
                continue
            m = CHANNEL_FILE.match(f.name) if f.is_file() else None
            if not m:
                report(f, f"{f.name}: expected only {CHANNELS} and <channel>.jsonc files")
            elif m.group(1) not in ("prod", "dev") and m.group(1) not in channels:
                report(f, f"{f.name}: {m.group(1)} isn't a channel: add it to {CHANNELS}, or remove the file")
            else:
                configs[m.group(1)] = f
        if channels.get("prod") and "prod" not in configs:
            report(v, "prod.jsonc is required: there are prod releases")
        if not configs:
            report(v, "needs prod.jsonc, or dev.jsonc until the first promotion")
        for channel, f in configs.items():
            errors = check_file(f, config_schema, platforms, args.owner, channel == "prod")
            for e in errors:
                report(f, e)
            if not errors:
                print(f"{f}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
