"""Prints the release tags of a repo that a remote config on main names, one
per line, in any public Open Logix repo, any config version and any
channel (remote-config/<product>/<NNN>/<channel>.jsonc). publish-pre-release
never deletes those pre-releases (Remote updates, Release cleanup, in the
website repo's CLAUDE.md).

    python3 referenced_tags.py <owner/repo>

Reads with the gh CLI (`gh api`), read-only: the configs are public. A
failure to read any of them is an error, so nothing is deleted on partial
information.
"""

import json
import re
import subprocess
import sys

CONFIG = re.compile(r"^remote-config/[^/]+/[0-9]{3}/(?!channels\.jsonc$)[a-z][a-z0-9-]*\.jsonc$")


def gh_api(*args: str) -> str:
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"gh api {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def strip_comments(text: str) -> str:
    out, i, n, in_string = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            in_string = c != '"'
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
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = sys.argv[1]
    org = target.split("/")[0]
    tags = set()
    for info in json.loads(gh_api(f"orgs/{org}/repos?type=public&per_page=100")):
        if info.get("archived"):
            continue
        repo, branch = info["full_name"], info["default_branch"]
        tree = json.loads(gh_api(f"repos/{repo}/git/trees/{branch}?recursive=1"))
        for item in tree.get("tree", []):
            if item["type"] != "blob" or not CONFIG.match(item["path"]):
                continue
            text = gh_api("-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{item['path']}?ref={branch}")
            try:
                config = json.loads(strip_comments(text))
            except ValueError:
                continue  # The repo's own CI reports a broken config.
            for name, dep in config.items():
                if name != "$schema" and isinstance(dep, dict) and dep.get("repo") == target and dep.get("release"):
                    tags.add(dep["release"])
    for tag in sorted(tags):
        print(tag)


if __name__ == "__main__":
    main()
