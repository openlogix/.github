"""Writes SHA256SUMS and RELEASE-BODY.md for publish-pre-release: the release
text in the Open Logix structure (website repo's CLAUDE.md, "Release text is
user facing"), so every repo's release page reads the same way.

    release_text.py  (inputs come from the environment, set by action.yaml)

Structure: title line, links, the "Use it through Logix Engine" note, the
repo's own content, Build info, About these releases, Checksums.
"""

import hashlib
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The "Use it through Logix Engine" notes, the one copy of their wording.
NOTES = {
    "catalog": (
        "> [!IMPORTANT]\n"
        "> **Use it through Logix Engine.** The intended way for tools, scripts and AI agents "
        "to read Logix Catalog is [Logix Engine](https://www.openlogix.org/engine/)'s API "
        "(`get_catalog`), which downloads the catalog and serves it. The data is free to use "
        "and copy under MIT or Apache 2.0, but reading the files directly is at your own risk: "
        "their layout may change between releases."
    ),
    "core": (
        "> [!IMPORTANT]\n"
        "> **Use it through Logix Engine.** logix-core is meant only for "
        "[Logix Engine](https://www.openlogix.org/engine/), which downloads it and runs its "
        "tools. It isn't for use on its own, and its content and layout may change at any time."
    ),
}


def policy_keep() -> int:
    text = (HERE.parent / "release-policy.sh").read_text()
    return int(re.search(r"^PRERELEASE_KEEP=(\d+)", text, re.M).group(1))


def about(extra: list[str]) -> str:
    keep = policy_keep()
    bullets = [f"These are pre-releases: development builds, made daily. Only the {keep} newest "
               f"are kept, so each one is deleted after about {keep} days, unless it's made stable."]
    bullets += extra
    bullets.append("To make a pre-release stable, edit it and change its status from "
                   "`Pre-release` to `None`.")
    return "### About these releases\n\n" + "\n".join(f"- {b}" for b in bullets)


def build_info_text(info: dict) -> str:
    width = max(len(k) for k in info) + 3
    return "\n".join(f"{k.ljust(width)}: {v}" for k, v in info.items())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lines(name: str) -> list[str]:
    return [l.strip() for l in os.environ.get(name, "").splitlines() if l.strip()]


def main() -> None:
    files = [Path(f) for f in lines("FILES")]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise SystemExit("Missing release files: " + ", ".join(missing))
    sums = "".join(f"{sha256(f)}  {f.name}\n" for f in files)
    Path("SHA256SUMS").write_text(sums)

    info = json.loads(Path("BUILD-INFO.json").read_text())
    note = os.environ.get("ENGINE_NOTE", "").strip()
    if note and note not in NOTES:
        raise SystemExit(f"engine-note must be one of {sorted(NOTES)}, not {note!r}")
    content_file = os.environ.get("CONTENT_FILE", "").strip()

    parts = [f"**{os.environ['TITLE'].strip()}**"]
    if os.environ.get("LINKS", "").strip():
        parts.append(os.environ["LINKS"].strip())
    if note:
        parts.append(NOTES[note])
    if content_file:
        parts.append(Path(content_file).read_text().strip())
    parts.append("### Build info\n\n```\n" + build_info_text(info) + "\n```")
    parts.append(about(lines("ABOUT_BULLETS")))
    parts.append("### Checksums\n\n```\n" + sums.strip() + "\n```")
    Path("RELEASE-BODY.md").write_text("\n\n".join(parts) + "\n")
    print(Path("RELEASE-BODY.md").read_text())


if __name__ == "__main__":
    main()
