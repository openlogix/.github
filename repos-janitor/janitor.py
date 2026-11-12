"""repos-janitor: checks across the Open Logix repos what no single repo's CI
can see, and reports problems in one place (modeled on Apio's janitor). The
design and the checks are in this repo's CLAUDE.md (repos-janitor).

    python3 repos-janitor/janitor.py --state <file>

Reads every public, unarchived repo in the org with the gh CLI (`gh api`,
`gh release download`), read-only. Each finding is "fail" (broken now) or
"warn" (breaks soon, or needs a person); they're printed, written as GitHub
annotations and, in a workflow, as a table in the run's summary. The exit
status is 1 if anything failed. --state is a JSON file of the SHA-256s seen
in the last run (check 4), read if it exists and written at the end.

For tests: JANITOR_NOW (an ISO time) sets the current time.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORG = "openlogix"
# The platforms remote configs must serve. Add windows-x86-64 and
# linux-x86-64 with Milestones 3 and 4.
PLATFORMS = ["darwin-arm64"]
# Warn when a named pre-release is deleted within this many days.
EXPIRY_WARN_DAYS = 2
# Warn when a named release is this many days older than the newest one.
STALE_DAYS = 7
# Warn when a scheduled workflow hasn't run for this long.
RUN_MAX_AGE = dt.timedelta(hours=26)
WEBSITE = "openlogix.github.io"
# The repos whose schemas/ own the website's docs/schemas/ copies.
SCHEMA_OWNERS = ["logix-catalog", ".github"]
CONFIG = re.compile(r"^remote-config/[^/]+/config-[0-9]{3}\.jsonc$")
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
DATE_TAG = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

sys.path.insert(0, str(ROOT / "actions" / "check-remote-config"))
from check_remote_config import strip_comments  # noqa: E402


def now() -> dt.datetime:
    value = os.environ.get("JANITOR_NOW")
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc)


def keep_count() -> int:
    """PRERELEASE_KEEP from the shared release policy."""
    text = (ROOT / "actions" / "release-policy.sh").read_text()
    return int(re.search(r"^PRERELEASE_KEEP=([0-9]+)", text, re.M).group(1))


def gh(*args: str) -> tuple[int, str]:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    return r.returncode, r.stdout if r.returncode == 0 else r.stderr


def api(path: str):
    """A GitHub API GET, as JSON; None for a 404."""
    status, out = gh("api", path)
    if status != 0:
        if "404" in out:
            return None
        raise RuntimeError(f"gh api {path}: {out.strip()}")
    return json.loads(out)


def raw(repo: str, path: str, ref: str) -> str:
    """A file's text on a branch."""
    status, out = gh(
        "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={ref}"
    )
    if status != 0:
        raise RuntimeError(f"can't read {repo}/{path}: {out.strip()}")
    return out


def sums(repo: str, tag: str) -> dict[str, str] | None:
    """A release's SHA256SUMS, file name to SHA-256; None without one."""
    status, out = gh("release", "download", tag, "-R", repo, "-p", "SHA256SUMS", "-O", "-")
    if status != 0:
        return None
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[1].lstrip("*")] = parts[0].lower()
    return result


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, level: str, repo: str, check: str, message: str) -> None:
        self.rows.append((level, repo, check, message))

    def failed(self) -> bool:
        return any(r[0] == "fail" for r in self.rows)


class Repo:
    """One repo: its default branch, file list and releases (read once)."""

    def __init__(self, info: dict) -> None:
        self.name = info["full_name"]
        self.short = info["name"]
        self.branch = info["default_branch"]
        tree = api(f"repos/{self.name}/git/trees/{self.branch}?recursive=1") or {}
        self.files = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
        self._releases = None

    def releases(self) -> list[dict]:
        if self._releases is None:
            self._releases = [r for r in api(f"repos/{self.name}/releases?per_page=100") or [] if not r["draft"]]
        return self._releases


def check_configs(repo: Repo, report: Report, platforms: list[str]) -> list[dict]:
    """Check 1: the repo's remote configs pass check-remote-config. Returns
    their entries, for checks 2 to 4."""
    paths = [p for p in repo.files if CONFIG.match(p)]
    if not paths:
        return []
    entries = []
    with tempfile.TemporaryDirectory() as tmp:
        for p in paths:
            text = raw(repo.name, p, repo.branch)
            dest = Path(tmp) / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            try:
                config = json.loads(strip_comments(text))
                for name, dep in config.items():
                    if name != "$schema" and isinstance(dep, dict):
                        entries.append({"config": p, "name": name, **dep})
            except (ValueError, AttributeError):
                pass
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "actions" / "check-remote-config" / "check_remote_config.py"),
                "--folder", str(Path(tmp) / "remote-config"),
                "--schema", str(ROOT / "schemas" / "logix-remote-config-v1.json"),
                "--platforms", " ".join(platforms),
                "--owner", ORG,
            ],
            capture_output=True,
            text=True,
        )
    if r.returncode != 0:
        for line in (r.stdout + r.stderr).splitlines():
            m = re.match(r"^::error(?: file=([^:]*))?::(.*)$", line)
            if m:
                where = m.group(1) or ""
                where = where.split(tmp + "/", 1)[-1]
                report.add("fail", repo.name, "remote configs", f"{where}: {m.group(2)}" if where else m.group(2))
    return entries


def tag_date(tag: str) -> dt.date | None:
    return dt.date.fromisoformat(tag) if DATE_TAG.match(tag) else None


def check_pins(repo: Repo, entries: list[dict], repos: dict[str, Repo], report: Report,
               platforms: list[str], state: dict, new_state: dict) -> None:
    """Checks 2 (expiring pre-releases), 3 (stale pins) and 4 (rebuilt
    releases) for each dependency a config names."""
    keep = keep_count()
    for e in entries:
        dep = repos.get(e.get("repo", ""))
        tag = e.get("release", "")
        where = f"{e['config']}: {e['name']} {tag}"
        if dep is None:
            continue  # Check 1 reports a repo outside the org.
        named = next((r for r in dep.releases() if r["tag_name"] == tag), None)
        if named is None:
            continue  # Check 1 reports it.
        if named["prerelease"]:
            pre = sorted((r["tag_name"] for r in dep.releases() if r["prerelease"]), reverse=True)
            left = keep - pre.index(tag)
            if left <= EXPIRY_WARN_DAYS:
                report.add("warn", repo.name, "expiring pre-releases",
                           f"{where} is a pre-release deleted in about {left} day(s): repin it")
        dates = [d for d in (tag_date(r["tag_name"]) for r in dep.releases()) if d]
        named_date = tag_date(tag)
        if dates and named_date and (max(dates) - named_date).days > STALE_DAYS:
            report.add("warn", repo.name, "stale pins",
                       f"{where} is {(max(dates) - named_date).days} days older than {dep.name}'s newest release, {max(dates)}")
        file_sums = sums(dep.name, tag) or {}
        for platform in platforms:
            file = e.get("file", "").replace("${PLATFORM}", platform).replace("${YYYYMMDD}", tag.replace("-", ""))
            sha = file_sums.get(file)
            if not sha:
                continue  # Check 1 reports it.
            key = f"{dep.name}/{tag}/{file}"
            new_state[key] = sha
            if key in state and state[key] != sha:
                report.add("warn", repo.name, "rebuilt releases",
                           f"{where}: {file} was rebuilt under the same tag (its SHA-256 changed), so installed products download it again")


def check_schemas(repos: dict[str, Repo], report: Report) -> None:
    """Check 5: the website's schema copies equal their owners' files."""
    site = repos.get(f"{ORG}/{WEBSITE}")
    if site is None:
        return
    for path in (p for p in site.files if p.startswith("docs/schemas/") and p.endswith(".json")):
        name = path.rsplit("/", 1)[1]
        owner = next((repos[f"{ORG}/{o}"] for o in SCHEMA_OWNERS
                      if f"{ORG}/{o}" in repos and f"schemas/{name}" in repos[f"{ORG}/{o}"].files), None)
        if owner is None:
            report.add("fail", site.name, "schema copies", f"{path} has no owner (schemas/{name} in {', '.join(SCHEMA_OWNERS)})")
        elif raw(site.name, path, site.branch) != raw(owner.name, f"schemas/{name}", owner.branch):
            report.add("fail", site.name, "schema copies", f"{path} differs from {owner.name}'s schemas/{name}: copy it again")


def check_runs(repo: Repo, report: Report) -> None:
    """Check 6: every scheduled workflow's latest scheduled run on the
    default branch passed, and ran recently."""
    for path in (p for p in repo.files if WORKFLOW.match(p)):
        file = path.rsplit("/", 1)[1]
        if repo.short == ".github" and file.startswith("repos-janitor."):
            continue  # Itself: a failure here would only repeat.
        if not re.search(r"^\s*schedule:", raw(repo.name, path, repo.branch), re.M):
            continue
        runs = api(f"repos/{repo.name}/actions/workflows/{file}/runs?branch={repo.branch}&event=schedule&status=completed&per_page=1")
        run = (runs or {}).get("workflow_runs", [None])[0] if runs and runs.get("workflow_runs") else None
        if run is None:
            report.add("warn", repo.name, "daily runs", f"{file} has no completed scheduled run yet")
            continue
        if run["conclusion"] != "success":
            report.add("fail", repo.name, "daily runs", f"{file}'s last scheduled run {run['conclusion']}: {run['html_url']}")
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if now() - started > RUN_MAX_AGE:
            report.add("warn", repo.name, "daily runs", f"{file} hasn't run since {run['created_at']}")


def check_releases(repo: Repo, report: Report) -> None:
    """Check 7: every release has SHA256SUMS and BUILD-INFO.json, and has
    every file its SHA256SUMS lists. Only date-tagged releases: others,
    such as the test releases actions-test makes here and deletes, aren't
    Open Logix releases."""
    for r in (r for r in repo.releases() if DATE_TAG.match(r["tag_name"])):
        assets = {a["name"] for a in r["assets"]}
        missing = [f for f in ("SHA256SUMS", "BUILD-INFO.json") if f not in assets]
        if missing:
            report.add("fail", repo.name, "releases", f"{r['tag_name']} has no {' or '.join(missing)}")
        listed = sums(repo.name, r["tag_name"]) or {}
        for f in sorted(set(listed) - assets):
            report.add("fail", repo.name, "releases", f"{r['tag_name']}'s SHA256SUMS lists {f}, which the release doesn't have")


def write_summary(report: Report, checked: int) -> None:
    lines = [f"## repos-janitor: {checked} repos"]
    if not report.rows:
        lines.append("\nEverything passed.")
    else:
        lines += ["", "| Result | Repo | Check | Problem |", "| --- | --- | --- | --- |"]
        for level, repo, check, message in sorted(report.rows):
            lines.append(f"| {level} | [{repo}](https://github.com/{repo}) | {check} | {message.replace('|', '\\|')} |")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", required=True, help="the last run's SHA-256s (JSON), read and written")
    args = ap.parse_args()
    state_file = Path(args.state)
    state = json.loads(state_file.read_text()) if state_file.is_file() else {}
    new_state: dict[str, str] = {}

    infos = [i for i in api(f"orgs/{ORG}/repos?type=public&per_page=100") or [] if not i["archived"]]
    repos = {i["full_name"]: Repo(i) for i in infos}
    report = Report()
    for repo in sorted(repos.values(), key=lambda r: r.name):
        entries = check_configs(repo, report, PLATFORMS)
        check_pins(repo, entries, repos, report, PLATFORMS, state, new_state)
        check_runs(repo, report)
        check_releases(repo, report)
    check_schemas(repos, report)

    state_file.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n")
    for level, repo, check, message in sorted(report.rows):
        kind = "error" if level == "fail" else "warning"
        print(f"::{kind} title={check} ({repo})::{message}")
    if not report.rows:
        print(f"repos-janitor: {len(repos)} repos, everything passed.")
    write_summary(report, len(repos))
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
