"""Checks a repo's remote configs: remote-config/<product>/config-NNN.jsonc.

For each file: its name, its JSONC syntax, the schema (logix-remote-config-v1,
in this repo's schemas/), and for each dependency: its repo's owner (when
--owner is given), that its release exists and is stable (not a pre-release
or a draft), that its file exists for every platform, and that the release's
SHA256SUMS lists it. Every problem is reported, as a GitHub annotation, and
the exit status is 1 if there were any.

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

FILE_NAME = re.compile(r"^config-[0-9]{3}\.jsonc$")
PRODUCT = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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


def check_dependency(name: str, dep: dict, platforms: list[str], owner: str) -> list[str]:
    repo, tag, template = dep["repo"], dep["release"], dep["file"]
    errors = []
    if owner and repo.split("/")[0] != owner:
        errors.append(f"{name}: {repo} isn't in {owner} (only allowed off main)")
    status, out = gh("api", f"repos/{repo}/releases/tags/{tag}")
    if status != 0:
        return errors + [f"{name}: no release {tag} in {repo} ({out.strip()})"]
    release = json.loads(out)
    if release.get("prerelease") or release.get("draft"):
        errors.append(f"{name}: {repo} {tag} is a pre-release; name only stable releases")
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


def check_file(path: Path, schema: dict, platforms: list[str], owner: str) -> list[str]:
    try:
        config = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except (ValueError, UnicodeDecodeError) as e:
        return [f"not valid JSONC: {e}"]
    problems = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: list(e.path)
    )
    if problems:
        return [
            f"{'/'.join(str(p) for p in e.path) or '(top)'}: {e.message}" for e in problems
        ]
    errors = []
    for name, dep in config.items():
        if name != "$schema":
            errors += check_dependency(name, dep, platforms, owner)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", required=True, help="the remote-config folder")
    ap.add_argument("--schema", required=True, help="logix-remote-config-v1.json")
    ap.add_argument("--platforms", required=True, help="space-separated, such as darwin-arm64")
    ap.add_argument("--owner", default="", help="the only owner repos may have; empty for any")
    args = ap.parse_args()
    folder = Path(args.folder)
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
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

    files = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_dir() or not PRODUCT.match(entry.name):
            report(entry, f"{entry.name}: expected only product folders, such as logix-ide")
            continue
        for f in sorted(entry.iterdir()):
            if f.is_file() and FILE_NAME.match(f.name):
                files.append(f)
            else:
                report(f, f"{f.name}: expected only config-NNN.jsonc files")
    if not files:
        report(folder, "no remote config files")
    for f in files:
        errors = check_file(f, schema, platforms, args.owner)
        for e in errors:
            report(f, e)
        if not errors:
            print(f"{f}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
