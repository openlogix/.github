"""repos-janitor: checks across the Open Logix repos what no single repo's CI
can see, and reports problems in one place (modeled on Apio's janitor). The
design and the checks are in this repo's CLAUDE.md (repos-janitor).

    python3 repos-janitor/janitor.py --state <file> --summary <file>

Reads every public, unarchived repo in the org with the gh CLI (`gh api`,
`gh release download`), read-only. Each finding is "fail" (broken now) or
"warn" (breaks soon, or needs a person); the problems are printed as GitHub
annotations, and --summary gets the whole report in Markdown, which the
workflow adds to the run's summary page. The exit
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
RUN_WHY = "It runs daily to catch breakage even without a push; the repo's status badge shows it."
# Warn when a scheduled workflow hasn't run for this long.
RUN_MAX_AGE = dt.timedelta(hours=26)
WEBSITE = "openlogix.github.io"
SCHEMA_WHY = "Files outside the site name it by its www.openlogix.org URL, so it must equal its owner's."
# The repos whose schemas/ own the website's docs/schemas/ copies.
SCHEMA_OWNERS = ["logix-catalog", ".github"]
CONFIG_WHY = "Every installed product on this config version reads it from `main`."
CONFIG = re.compile(r"^remote-config/[^/]+/config-[0-9]{3}\.jsonc$")
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
# Repo types (the website repo's CLAUDE.md, Repo types): one GitHub topic
# per repo.
TYPES = {"openlogix-installable": "installable", "openlogix-dependency": "dependency",
         "openlogix-no-releases": "no releases"}
TYPE_WHY = "The repo type (its topic) decides its release policy (the website repo's CLAUDE.md, Repo types)."
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


def short(repo: str) -> str:
    """A repo's name without the org, for the report."""
    return repo.split("/", 1)[-1]


def release_file(repo: str, tag: str, name: str) -> str | None:
    """A release file's text; None without one."""
    status, out = gh("release", "download", tag, "-R", repo, "-p", name, "-O", "-")
    return out if status == 0 else None


def blob(repo: str, branch: str, path: str) -> str:
    return f"https://github.com/{repo}/blob/{branch}/{path}"


def release_page(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/releases/tag/{tag}"


class Report:
    """Every item checked: "pass", "warn" (breaks soon, or needs a person)
    or "fail" (broken now), with links to where to look or fix it."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str, list[tuple[str, str]], str]] = []

    def add(self, level: str, repo: str, check: str, message: str, links=(), why: str = "") -> None:
        """`why` says why the check applies to this item, such as the remote
        config that names a release."""
        self.rows.append((level, repo, check, message, list(links), why))

    def problems(self):
        order = {"fail": 0, "warn": 1}
        return sorted((r for r in self.rows if r[0] != "pass"), key=lambda r: (order[r[0]], r[1], r[2], r[3]))

    def count(self, level: str) -> int:
        return sum(1 for r in self.rows if r[0] == level)

    def failed(self) -> bool:
        return self.count("fail") > 0


class Repo:
    """One repo: its default branch, file list and releases (read once)."""

    def __init__(self, info: dict) -> None:
        self.name = info["full_name"]
        self.short = info["name"]
        self.branch = info["default_branch"]
        self.types = sorted(TYPES[t] for t in info.get("topics", []) if t in TYPES)
        self.type = self.types[0] if len(self.types) == 1 else None
        tree = api(f"repos/{self.name}/git/trees/{self.branch}?recursive=1") or {}
        self.files = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
        self._releases = None

    def latest(self) -> str | None:
        """The tag of the release GitHub marks Latest; None when there's no
        stable release (GitHub has no Latest then)."""
        if not hasattr(self, "_latest"):
            r = api(f"repos/{self.name}/releases/latest")
            self._latest = r["tag_name"] if r else None
        return self._latest

    def releases(self) -> list[dict]:
        if self._releases is None:
            # Date-tagged releases only: others, such as the test releases
            # actions-test makes in .github and deletes, aren't Open Logix
            # releases.
            self._releases = [r for r in api(f"repos/{self.name}/releases?per_page=100") or []
                              if not r["draft"] and DATE_TAG.match(r["tag_name"])]
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
    bad = set()
    for line in (r.stdout + r.stderr).splitlines():
        m = re.match(r"^::error(?: file=([^:]*))?::(.*)$", line)
        if m:
            where = (m.group(1) or "").split(tmp + "/", 1)[-1]
            bad.add(where)
            links = [("the config", blob(repo.name, repo.branch, where))] if where in paths else []
            report.add("fail", repo.name, "remote configs", f"`{where}`: {m.group(2)}" if where else m.group(2), links,
                       why=CONFIG_WHY)
    if r.returncode != 0 and not bad:
        report.add("fail", repo.name, "remote configs", f"`check-remote-config` failed: {(r.stdout + r.stderr).strip()[-300:]}",
                   why=CONFIG_WHY)
        return entries
    for p in paths:
        if p not in bad and "" not in bad:
            report.add("pass", repo.name, "remote configs", f"`{p}` is valid, and every release and file it names exists",
                       [("the config", blob(repo.name, repo.branch, p))], why=CONFIG_WHY)
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
        where = f"`{e['config'].rsplit('/', 1)[-1]}` names `{e['name']}` `{tag}`, which"
        config_link = ("the config, to repin", blob(repo.name, repo.branch, e["config"]))
        named_by = "Installed products download what their remote config names."
        if dep is None:
            continue  # Check 1 reports a repo outside the org.
        named = next((r for r in dep.releases() if r["tag_name"] == tag), None)
        if named is None:
            continue  # Check 1 reports it.
        if named["prerelease"]:
            pre = sorted((r["tag_name"] for r in dep.releases() if r["prerelease"]), reverse=True)
            left = keep - pre.index(tag)
            links = [config_link, ("the release, to make it stable", release_page(dep.name, tag))]
            if left <= EXPIRY_WARN_DAYS:
                report.add("warn", repo.name, "expiring pre-releases",
                           f"{where} is a pre-release deleted in about {left} day(s): repin it, or make the release stable", links, why=named_by)
            else:
                report.add("pass", repo.name, "expiring pre-releases", f"{where} is a pre-release kept about {left} more days", links, why=named_by)
        else:
            report.add("pass", repo.name, "expiring pre-releases", f"{where} is a stable release",
                       [("the release", release_page(dep.name, tag))], why=named_by)
        dates = [d for d in (tag_date(r["tag_name"]) for r in dep.releases()) if d]
        named_date = tag_date(tag)
        if dates and named_date:
            behind = (max(dates) - named_date).days
            newest = ("the newest release", release_page(dep.name, max(dates).isoformat()))
            if behind > STALE_DAYS:
                report.add("warn", repo.name, "stale pins",
                           f"{where} is {behind} days older than the newest release, `{max(dates)}`", [config_link, newest], why=named_by)
            else:
                report.add("pass", repo.name, "stale pins",
                           f"{where} is {'the newest release' if behind == 0 else f'{behind} day(s) behind the newest, `{max(dates)}`'}",
                           [newest], why=named_by)
        file_sums = sums(dep.name, tag) or {}
        for platform in platforms:
            file = e.get("file", "").replace("${PLATFORM}", platform).replace("${YYYYMMDD}", tag.replace("-", ""))
            sha = file_sums.get(file)
            if not sha:
                continue  # Check 1 reports it.
            key = f"{dep.name}/{tag}/{file}"
            new_state[key] = sha
            links = [("the release", release_page(dep.name, tag))]
            if key in state and state[key] != sha:
                report.add("warn", repo.name, "rebuilt releases",
                           f"{where} was rebuilt under the same tag: `{file}`'s SHA-256 changed, so installed products download it again", links, why=named_by)
            else:
                report.add("pass", repo.name, "rebuilt releases",
                           f"{where} has `{file}` {'unchanged since the last run' if key in state else 'seen for the first time'}", links, why=named_by)


def check_schemas(repos: dict[str, Repo], report: Report) -> None:
    """Check 5: the website's schema copies equal their owners' files."""
    site = repos.get(f"{ORG}/{WEBSITE}")
    if site is None:
        return
    for path in (p for p in site.files if p.startswith("docs/schemas/") and p.endswith(".json")):
        name = path.rsplit("/", 1)[1]
        owner = next((repos[f"{ORG}/{o}"] for o in SCHEMA_OWNERS
                      if f"{ORG}/{o}" in repos and f"schemas/{name}" in repos[f"{ORG}/{o}"].files), None)
        copy = ("the copy", blob(site.name, site.branch, path))
        if owner is None:
            report.add("fail", site.name, "schema copies", f"`{path}` has no owner (`schemas/{name}` in {', '.join(f'`{o}`' for o in SCHEMA_OWNERS)})", [copy], why=SCHEMA_WHY)
            continue
        original = ("the owner's file", blob(owner.name, owner.branch, f"schemas/{name}"))
        if raw(site.name, path, site.branch) != raw(owner.name, f"schemas/{name}", owner.branch):
            report.add("fail", site.name, "schema copies", f"`{path}` differs from `{short(owner.name)}`'s `schemas/{name}`: copy it again", [original, copy], why=SCHEMA_WHY)
        else:
            report.add("pass", site.name, "schema copies", f"`{path}` equals `{short(owner.name)}`'s `schemas/{name}`", [original, copy], why=SCHEMA_WHY)


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
        workflow = ("the workflow", f"https://github.com/{repo.name}/actions/workflows/{file}")
        if run is None:
            report.add("warn", repo.name, "daily runs", f"`{file}` has no completed scheduled run yet", [workflow], why=RUN_WHY)
            continue
        links = [("the run", run["html_url"]), workflow]
        ok = run["conclusion"] == "success"
        if not ok:
            report.add("fail", repo.name, "daily runs", f"`{file}`'s last scheduled run: {run['conclusion']}", links, why=RUN_WHY)
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if now() - started > RUN_MAX_AGE:
            report.add("warn", repo.name, "daily runs", f"`{file}` hasn't run since {run['created_at']}", links, why=RUN_WHY)
        elif ok:
            report.add("pass", repo.name, "daily runs", f"`{file}`'s last scheduled run passed ({run['created_at']})", links, why=RUN_WHY)


def check_releases(repo: Repo, report: Report, named: dict[str, list[str]]) -> None:
    """Check 7: every release something refers to has SHA256SUMS and
    BUILD-INFO.json, and has every file its SHA256SUMS lists. As Apio's
    janitor, only releases people or products are pointed to: the ones a
    remote config on main names, and the repo's Latest (the one tutorials
    and install guides send people to). Other daily pre-releases are
    disposable. Later, also the releases on public listings (stores)."""
    latest = repo.latest()
    for r in repo.releases():
        tag = r["tag_name"]
        users = named.get(f"{repo.name}/{tag}", [])
        if not users and tag != latest:
            continue
        links = [("the release", release_page(repo.name, tag))]
        reasons = []
        if users:
            reasons.append(f"Named by {', '.join(users)}; installed products download it.")
        if tag == latest:
            reasons.append("Marked Latest: the release people are told to download.")
        why = " ".join(reasons)
        assets = {a["name"] for a in r["assets"]}
        problems = 0
        missing = [f for f in ("SHA256SUMS", "BUILD-INFO.json") if f not in assets]
        if missing:
            report.add("fail", repo.name, "releases", f"`{tag}` has no {' or '.join(f'`{m}`' for m in missing)}", links, why=why)
            problems += 1
        listed = sums(repo.name, tag) or {}
        for f in sorted(set(listed) - assets):
            report.add("fail", repo.name, "releases", f"`{tag}`'s `SHA256SUMS` lists `{f}`, which the release doesn't have", links, why=why)
            problems += 1
        if not problems:
            kind = "pre-release" if r["prerelease"] else "stable release"
            report.add("pass", repo.name, "releases", f"`{tag}` ({kind}) has `SHA256SUMS`, `BUILD-INFO.json` and every file it lists", links, why=why)


def md_links(links) -> str:
    return ", ".join(f"[{label}]({url})" for label, url in links)


def check_type(repo: Repo, report: Report) -> None:
    """Check 9: the repo has exactly one type topic; a "no releases" repo
    has no releases."""
    topics = ("`openlogix-installable`, `openlogix-dependency` or `openlogix-no-releases`")
    settings = [("the repo, to set its topic", f"https://github.com/{repo.name}")]
    if not repo.types:
        report.add("fail", repo.name, "repo type", f"it has no type topic: add one of {topics}", settings, why=TYPE_WHY)
        return
    if len(repo.types) > 1:
        report.add("fail", repo.name, "repo type", f"it has several types ({', '.join(repo.types)}): keep one", settings, why=TYPE_WHY)
        return
    topic = next(t for t, name in TYPES.items() if name == repo.type)
    report.add("pass", repo.name, "repo type", f"{repo.type} (topic `{topic}`)", settings, why=TYPE_WHY)
    if repo.type == "no releases":
        why = "Its type is \"no releases\": it's read, never downloaded."
        for r in repo.releases():
            report.add("fail", repo.name, "repo type", f"`{r['tag_name']}` is a release, but the repo has none by its type: delete it, or change the type",
                       [("the release", release_page(repo.name, r["tag_name"]))], why=why)
        if not repo.releases():
            report.add("pass", repo.name, "repo type", "it has no releases, as its type says", [], why=why)


def reachable_releases(repos: dict[str, Repo], report: Report) -> dict[str, dict[str, list[str]]]:
    """The dependency releases the recommended installs use (the website
    repo's CLAUDE.md, Repo types): from each installable repo's Latest, the
    releases its remote config names (the file of its `config-version`, in
    its BUILD-INFO.json, on main), directly or through another installable
    it names. Returns dependency repo -> tag -> how it's reached."""
    reach: dict[str, dict[str, list[str]]] = {}
    seen: set[tuple[str, str]] = set()

    def walk(repo: Repo, tag: str, via: str) -> None:
        if (repo.name, tag) in seen:
            return
        seen.add((repo.name, tag))
        text = release_file(repo.name, tag, "BUILD-INFO.json")
        try:
            info = json.loads(text) if text else {}
        except ValueError:
            info = {}
        version, product = info.get("config-version"), info.get("package-name")
        if not version or not product:
            report.add("warn", repo.name, "latest release",
                       f"`{tag}`'s `BUILD-INFO.json` has no `config-version`, so the releases it uses can't be found",
                       [("the release", release_page(repo.name, tag))],
                       why="A dependency's Latest follows the releases the recommended installs use.")
            return
        path = f"remote-config/{product}/config-{version}.jsonc"
        if path not in repo.files:
            return  # A config version without a file: check 1 covers the repo's files.
        config = json.loads(strip_comments(raw(repo.name, path, repo.branch)))
        for name, dep in config.items():
            if name == "$schema" or not isinstance(dep, dict) or dep.get("repo") not in repos:
                continue
            target, release = repos[dep["repo"]], dep.get("release", "")
            step = f"{via} → `{name}` `{release}`"
            if target.type == "dependency":
                reach.setdefault(target.name, {}).setdefault(release, []).append(step)
            elif target.type == "installable":
                walk(target, release, step)

    for repo in sorted(repos.values(), key=lambda r: r.name):
        if repo.type == "installable" and repo.latest():
            walk(repo, repo.latest(), f"`{repo.short}` `{repo.latest()}` (Latest)")
    return reach


def check_latest(repo: Repo, report: Report, reach: dict[str, dict[str, list[str]]]) -> None:
    """Check 8: the release marked Latest, by the repo's type (the website
    repo's CLAUDE.md, Repo types). An installable repo's Latest is for the
    people who install it, independent of other products' configs, so it's
    never compared with them; having none (only pre-releases) passes for
    now, as requiring stable releases is deferred. A dependency's Latest
    should be the newest release the recommended installs use (its
    reachable releases); while that one is a pre-release, none or an older
    stable one passes."""
    if not repo.releases() or repo.type not in ("installable", "dependency"):
        return
    latest = repo.latest()
    releases_link = ("the releases", f"https://github.com/{repo.name}/releases")
    if repo.type == "installable":
        why = "People are told to install the release marked Latest; for an installable it's independent of other products' configs."
        if latest is None:
            report.add("pass", repo.name, "latest release",
                       "no release is marked Latest yet: every release is a pre-release (required once stable releases are)",
                       [releases_link], why=why)
        else:
            report.add("pass", repo.name, "latest release",
                       f"`{latest}` is marked Latest, the release recommended to people who install it",
                       [("the release", release_page(repo.name, latest))], why=why)
        return
    # Only releases that exist: one a config names but that's missing is
    # check 1's failure.
    tags = {r["tag_name"] for r in repo.releases()}
    uses = {t: v for t, v in reach.get(repo.name, {}).items() if t in tags}
    why = "A dependency's Latest is the newest release the recommended installs (the installables' Latest) use."
    if uses:
        why += " Used: " + "; ".join(f"`{t}` by {', '.join(v)}" for t, v in sorted(uses.items(), reverse=True)) + "."
    if not uses:
        if latest is None:
            report.add("pass", repo.name, "latest release",
                       "no release is marked Latest, and no recommended install uses one yet", [releases_link], why=why)
        else:
            report.add("warn", repo.name, "latest release",
                       f"`{latest}` is marked Latest, but no recommended install uses a release of it: it sends people to a release no product uses",
                       [("the release", release_page(repo.name, latest)), releases_link], why=why)
        return
    target = max(uses)
    target_release = next((r for r in repo.releases() if r["tag_name"] == target), None)
    target_link = ("the release to mark Latest", release_page(repo.name, target))
    if latest == target:
        report.add("pass", repo.name, "latest release",
                   f"`{latest}` is marked Latest, the newest release the recommended installs use",
                   [("the release", release_page(repo.name, latest))], why=why)
    elif target_release is not None and target_release["prerelease"]:
        report.add("pass", repo.name, "latest release",
                   f"the newest release the recommended installs use, `{target}`, is a pre-release, so it can't be Latest yet",
                   [("the release", release_page(repo.name, target))], why=why)
    else:
        now_latest = f"`{latest}` is marked Latest" if latest else "no release is marked Latest"
        report.add("warn", repo.name, "latest release",
                   f"{now_latest}, but the newest release the recommended installs use is `{target}`: mark it Latest",
                   [target_link], why=why)


def write_summary(report: Report, checked: int, path: str) -> None:
    """The run's summary page, for people: one line with the counts, then
    the failures and the warnings, each with how to fix it and why it's
    checked, and last what passed, folded per repo."""
    fails, warns, passes = report.count("fail"), report.count("warn"), report.count("pass")
    status = "❌" if fails else "⚠️" if warns else "✅"
    lines = [
        f"# {status} repos-janitor",
        "",
        f"**{fails} failures · {warns} warnings · {passes} passed**, in {checked} repos "
        f"({now().strftime('%Y-%m-%d %H:%M')} UTC).",
    ]

    def item(check: str, message: str, links, why: str) -> list[str]:
        fix = f" ({md_links(links)})" if links else ""
        out = [f"- **{check}**: {message}{fix}"]
        if why:
            out.append(f"  <br><sub>Why: {why}</sub>")
        return out

    for level, title in (("fail", "❌ Failures"), ("warn", "⚠️ Warnings")):
        rows = [r for r in report.problems() if r[0] == level]
        if not rows:
            continue
        lines += ["", f"## {title}"]
        for repo in sorted({r[1] for r in rows}):
            lines += ["", f"### {short(repo)}", ""]
            for _, _, check, message, links, why in (r for r in rows if r[1] == repo):
                lines += item(check, message, links, why)
    if not fails and not warns:
        lines += ["", "No problems."]
    rows = sorted((r for r in report.rows if r[0] == "pass"), key=lambda r: (r[1], r[2], r[3]))
    lines += ["", "## ✅ Passed"]
    for repo in sorted({r[1] for r in rows}):
        mine = [r for r in rows if r[1] == repo]
        lines += ["", f"<details><summary><b>{short(repo)}</b>: {len(mine)} passed</summary>", ""]
        for _, _, check, message, links, why in mine:
            lines += item(check, message, links, why)
        lines += ["", "</details>"]
    Path(path).write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", required=True, help="the last run's SHA-256s (JSON), read and written")
    ap.add_argument("--summary", required=True, help="the Markdown report to write (the workflow adds it to the run's summary)")
    args = ap.parse_args()
    state_file = Path(args.state)
    state = json.loads(state_file.read_text()) if state_file.is_file() else {}
    new_state: dict[str, str] = {}

    infos = [i for i in api(f"orgs/{ORG}/repos?type=public&per_page=100") or [] if not i["archived"]]
    repos = {i["full_name"]: Repo(i) for i in infos}
    report = Report()
    # Releases the remote configs name: release -> "`repo`'s `config`".
    named: dict[str, list[str]] = {}
    for repo in sorted(repos.values(), key=lambda r: r.name):
        entries = check_configs(repo, report, PLATFORMS)
        for e in entries:
            named.setdefault(f"{e.get('repo')}/{e.get('release')}", []).append(f"`{repo.short}`'s `{e['config'].rsplit('/', 1)[-1]}`")
        check_pins(repo, entries, repos, report, PLATFORMS, state, new_state)
        check_runs(repo, report)
    reach = reachable_releases(repos, report)
    for repo in sorted(repos.values(), key=lambda r: r.name):
        check_type(repo, report)
        check_releases(repo, report, named)
        check_latest(repo, report, reach)
    check_schemas(repos, report)

    state_file.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n")
    for level, repo, check, message, links, _ in report.problems():
        kind = "error" if level == "fail" else "warning"
        # Annotations are plain text: no Markdown backticks.
        print(f"::{kind} title={check} ({repo})::{message.replace('`', '')} ({links[0][1] if links else 'no link'})")
    print(f"repos-janitor: {len(repos)} repos, {report.count('pass')} passed, "
          f"{report.count('warn')} warnings, {report.count('fail')} failures.")
    write_summary(report, len(repos), args.summary)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
