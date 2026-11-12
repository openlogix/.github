"""repos-janitor: checks across the Open Logix repos what no single repo's CI
can see, and reports it in one place (modeled on Apio's janitor). The design
is in this repo's CLAUDE.md (repos-janitor).

    python3 repos-janitor/janitor.py --summary <file> --results <file>

The model, as Apio's: a *requirement type* (such as release-should-be-stable)
has a title; every failed requirement is an error (no warnings: something
either needs fixing or it doesn't). Analyzers create *requirement
instances*: a type applied to one subject (a repo, a release, a config file,
a workflow), each with why it applies ([Why] notes; several analyzers can
add reasons to the same instance) and links to examine or fix it. Verifiers
then add the result ([Result] notes) and pass or fail it. Later, fixers may
act on failed instances.

Reads every public, unarchived repo in the org with the gh CLI (`gh api`,
`gh release download`), read-only. Writes --summary (Markdown, for the run's
summary page) and --results (JSON, for tools); prints a GitHub annotation
per failed instance. The exit status is 1 if any requirement failed.

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
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORG = "openlogix"
# The platforms remote configs must serve. Add windows-x86-64 and
# linux-x86-64 with Milestones 3 and 4.
PLATFORMS = ["darwin-arm64"]
# A scheduled workflow that hasn't run for this long fails (GitHub turns off
# a public repo's schedules after 60 days without activity).
RUN_MAX_AGE = dt.timedelta(hours=26)
WEBSITE = "openlogix.github.io"
# The repos whose schemas/ own the website's docs/schemas/ copies.
SCHEMA_OWNERS = ["logix-catalog", ".github"]
# A version folder's files: channels.jsonc and a config file per channel.
CONFIG_FILE = re.compile(r"^remote-config/([^/]+)/([^/]+)/([^/]+)$")
CHANNEL_CONFIG = re.compile(r"^remote-config/[^/]+/[0-9]{3}/(?!channels\.jsonc$)[a-z][a-z0-9-]*\.jsonc$")
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
# Repo types (the website repo's CLAUDE.md, Repo types): one GitHub topic
# per repo.
TYPES = {"openlogix-installable": "installable", "openlogix-dependency": "dependency",
         "openlogix-no-releases": "no releases"}
DATE_TAG = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

sys.path.insert(0, str(ROOT / "actions" / "check-remote-config"))
from check_remote_config import strip_comments  # noqa: E402


# ---------------------------------------------------------------- the model

# Requirement types: id -> title. Every failure is an error.
REQUIREMENTS = {
    "release-tag-should-be-a-date": "Release tag should be a date",
    "config-should-be-valid": "Config should be valid",
    "release-should-be-valid": "Release should be valid",
    "release-should-be-stable": "Release should be stable",
    "release-should-be-latest": "Release should be Latest",
    "latest-should-be-used": "Latest should be used by a recommended install",
    "latest-should-be-prod": "Latest should be a prod release",
    "dev-should-not-lag-behind-prod": "Dev config should not lag behind prod",
    "schema-copy-should-match": "Schema copy should match its owner",
    "daily-build-should-pass": "Daily build should pass",
    "daily-build-should-be-recent": "Daily build should be recent",
    "repo-should-have-one-type": "Repo should have one type",
    "repo-should-have-no-releases": "Repo should have no releases",
}


@dataclass
class Instance:
    """One requirement applied to one subject."""

    repo: str
    req: str
    # The subject, in Markdown (linked) and plain text, and its fields for
    # the JSON results (such as release_tag).
    subject_md: str
    subject: str
    fields: dict = field(default_factory=dict)
    why: list = field(default_factory=list)
    result: list = field(default_factory=list)
    passed: bool | None = None
    # (label, url) to examine or fix it.
    links: list = field(default_factory=list)

    def because(self, why: str) -> "Instance":
        if why not in self.why:
            self.why.append(why)
        return self

    def link(self, label: str, url: str) -> "Instance":
        if (label, url) not in self.links:
            self.links.append((label, url))
        return self

    def verdict(self, passed: bool, result: str) -> "Instance":
        self.passed = passed
        self.result = [result]
        return self


class Report:
    def __init__(self) -> None:
        self.instances: dict[tuple, Instance] = {}

    def require(self, repo: str, req: str, key: str, subject_md: str, subject: str, **fields) -> Instance:
        """The instance of `req` for this subject (`key`), created on first
        use: analyzers that reach the same subject add their reasons to it."""
        k = (repo, req, key)
        if k not in self.instances:
            self.instances[k] = Instance(repo, req, subject_md, subject, fields)
        return self.instances[k]

    def done(self) -> list[Instance]:
        return [i for i in self.instances.values() if i.passed is not None]

    def failed(self) -> list[Instance]:
        return [i for i in self.done() if not i.passed]

    def passed(self) -> list[Instance]:
        return [i for i in self.done() if i.passed]


# ---------------------------------------------------------------- GitHub

def now() -> dt.datetime:
    value = os.environ.get("JANITOR_NOW")
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc)


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


def release_file(repo: str, tag: str, name: str) -> str | None:
    """A release file's text; None without one."""
    status, out = gh("release", "download", tag, "-R", repo, "-p", name, "-O", "-")
    return out if status == 0 else None


def sums(repo: str, tag: str) -> dict[str, str] | None:
    """A release's SHA256SUMS, file name to SHA-256; None without one."""
    text = release_file(repo, tag, "SHA256SUMS")
    if text is None:
        return None
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[1].lstrip("*")] = parts[0].lower()
    return result


def blob(repo: str, branch: str, path: str) -> str:
    return f"https://github.com/{repo}/blob/{branch}/{path}"


def release_page(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/releases/tag/{tag}"


def releases_page(repo: str) -> str:
    return f"https://github.com/{repo}/releases"


def tag_date(tag: str) -> dt.date | None:
    return dt.date.fromisoformat(tag) if DATE_TAG.match(tag) else None


def config_name(path: str) -> str:
    """A config file as the report names it: its version folder and name,
    such as 001/dev.jsonc."""
    return "/".join(path.split("/")[-2:])


class Repo:
    """One repo: its type, default branch, file list and releases (read once)."""

    def __init__(self, info: dict) -> None:
        self.name = info["full_name"]
        self.short = info["name"]
        self.branch = info["default_branch"]
        self.topics = sorted(t for t in info.get("topics", []) if t in TYPES)
        self.type = TYPES[self.topics[0]] if len(self.topics) == 1 else None
        tree = api(f"repos/{self.name}/git/trees/{self.branch}?recursive=1") or {}
        self.files = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
        self._releases = None
        self._latest = "unread"
        self._channels = None

    def latest(self) -> str | None:
        """The tag GitHub marks Latest; None without a stable release, or
        when it isn't date-tagged (release-tag-should-be-a-date reports it)."""
        if self._latest == "unread":
            r = api(f"repos/{self.name}/releases/latest")
            self._latest = r["tag_name"] if r and DATE_TAG.match(r["tag_name"]) else None
        return self._latest

    def all_releases(self) -> list[dict]:
        """Every published release (no drafts), whatever its tag."""
        if self._releases is None:
            self._releases = [r for r in api(f"repos/{self.name}/releases?per_page=100") or [] if not r["draft"]]
        return self._releases

    def releases(self) -> list[dict]:
        """The date-tagged releases, which the other checks look at; any other
        tag fails release-tag-should-be-a-date."""
        return [r for r in self.all_releases() if DATE_TAG.match(r["tag_name"])]

    def release(self, tag: str) -> dict | None:
        return next((r for r in self.releases() if r["tag_name"] == tag), None)

    def channels(self) -> dict[str, dict[str, list[str]]]:
        """Per version folder (such as 001), its channels.jsonc lists."""
        if self._channels is None:
            self._channels = {}
            for p in self.files:
                m = CONFIG_FILE.match(p)
                if m and m.group(3) == "channels.jsonc":
                    try:
                        data = json.loads(strip_comments(raw(self.name, p, self.branch)))
                    except ValueError:
                        continue  # config-should-be-valid reports it.
                    self._channels[m.group(2)] = {
                        k: v for k, v in data.items() if k != "$schema" and isinstance(v, list)}
        return self._channels

    def prod_tags(self) -> dict[str, str]:
        """This repo's prod releases: tag -> its version folder."""
        return {t: v for v, chans in self.channels().items() for t in chans.get("prod", [])}

    def channels_path(self, version: str) -> str | None:
        return next((p for p in self.files if p.endswith(f"/{version}/channels.jsonc")), None)


def req_release(repo: Repo, tag: str, report: Report, req: str) -> Instance:
    return report.require(repo.name, req, tag, f"[`{tag}`]({release_page(repo.name, tag)})", tag,
                          release_tag=tag).link("the release", release_page(repo.name, tag))


# ---------------------------------------------------------------- analyzers and verifiers

def check_configs(repo: Repo, report: Report) -> list[dict]:
    """config-should-be-valid: each version folder's files pass
    check-remote-config. Returns the entries of the channel config files."""
    paths = [p for p in repo.files if CONFIG_FILE.match(p)]
    if not paths:
        return []
    for p in paths:
        product, version, name = CONFIG_FILE.match(p).groups()
        if name == "channels.jsonc":
            why = f"It lists which {product} releases are on which channel, for config {version}."
        elif CHANNEL_CONFIG.match(p):
            channel = name[: -len(".jsonc")]
            why = (f"{product} releases on config {version} read it from `main`, on the {channel} channel"
                   + (" and on every channel without a file of its own." if channel == "prod" else "."))
        else:
            why = f"It's in {product}'s remote config folder."
        url = blob(repo.name, repo.branch, p)
        report.require(repo.name, "config-should-be-valid", p, f"[`{config_name(p)}`]({url})",
                       config_name(p), file=p).because(why).link("the file", url)
    entries = []
    with tempfile.TemporaryDirectory() as tmp:
        for p in paths:
            text = raw(repo.name, p, repo.branch)
            dest = Path(tmp) / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            if not CHANNEL_CONFIG.match(p):
                continue
            try:
                config = json.loads(strip_comments(text))
                for name, dep in config.items():
                    if name != "$schema" and isinstance(dep, dict):
                        entries.append({"config": p, "name": name, **dep})
            except (ValueError, AttributeError):
                pass
        r = subprocess.run(
            [sys.executable, str(ROOT / "actions" / "check-remote-config" / "check_remote_config.py"),
             "--folder", str(Path(tmp) / "remote-config"), "--schemas", str(ROOT / "schemas"),
             "--repo", repo.name, "--platforms", " ".join(PLATFORMS), "--owner", ORG],
            capture_output=True, text=True)
    problems: dict[str, list[str]] = {}
    for line in (r.stdout + r.stderr).splitlines():
        m = re.match(r"^::error(?: file=([^:]*))?::(.*)$", line)
        if m:
            where = (m.group(1) or "").split(tmp + "/", 1)[-1]
            problems.setdefault(where if where in paths else "", []).append(m.group(2))
    for p in paths:
        inst = report.require(repo.name, "config-should-be-valid", p, "", "")
        if problems.get(p):
            inst.verdict(False, " ".join(problems[p]))
        elif problems.get(""):
            inst.verdict(False, "Its folder has problems: " + " ".join(problems[""]))
        else:
            inst.verdict(True, "Valid" + (", and every release and file it names exists."
                                          if CHANNEL_CONFIG.match(p) else "."))
    return entries


def check_pins(repo: Repo, entries: list[dict], repos: dict[str, Repo], report: Report) -> None:
    """dev-should-not-lag-behind-prod: a dev config naming an older release
    than its version's prod config (a dev file left over after a promotion)."""
    prod_pins = {(e["config"].rsplit("/", 1)[0], e["name"]): e for e in entries
                 if e["config"].endswith("/prod.jsonc")}
    for e in entries:
        dep = repos.get(e.get("repo", ""))
        tag = e.get("release", "")
        if dep is None or dep.release(tag) is None:
            continue  # config-should-be-valid reports it.
        cfg = config_name(e["config"])
        cfg_url = blob(repo.name, repo.branch, e["config"])
        channel = cfg.split("/")[1][: -len(".jsonc")]
        prod = prod_pins.get((e["config"].rsplit("/", 1)[0], e["name"]))
        if channel == "dev" and prod and prod.get("repo") == e.get("repo") and DATE_TAG.match(prod.get("release", "")):
            prod_cfg = config_name(prod["config"])
            prod_url = blob(repo.name, repo.branch, prod["config"])
            inst = report.require(
                repo.name, "dev-should-not-lag-behind-prod", f"{e['config']}|{e['name']}",
                f"[`{cfg}`]({cfg_url}) → {e['name']} [`{tag}`]({release_page(dep.name, tag)})",
                f"{cfg} → {e['name']} {tag}", config=cfg, dependency=e["name"], release_tag=tag)
            inst.because(f"Dev releases download what [`{cfg}`]({cfg_url}) names; without it they'd use "
                         f"[`{prod_cfg}`]({prod_url}), so dev should never be older than prod.")
            inst.link("the dev config, to repin or delete", cfg_url).link("the prod config", prod_url)
            if tag < prod["release"]:
                inst.verdict(False, f"It's older than prod's `{prod['release']}`: probably left over after a promotion. "
                                    f"Delete `{cfg}` (dev then uses prod's), or repin it.")
            else:
                inst.verdict(True, "It's the same as prod's." if tag == prod["release"]
                             else f"It's newer than prod's `{prod['release']}`.")


def check_releases(repos: dict[str, Repo], entries_by_repo: dict[str, list[dict]], report: Report) -> None:
    """release-should-be-valid and release-should-be-stable, for the
    releases something refers to (as Apio's janitor): named by a remote
    config on main, on a prod list, or marked Latest. Other daily
    pre-releases are disposable."""
    for repo in repos.values():
        for e in entries_by_repo.get(repo.name, []):
            dep = repos.get(e.get("repo", ""))
            if dep is None:
                continue
            tag = e.get("release", "")
            cfg = config_name(e["config"])
            named = f"Named by {repo.short} [`{cfg}`]({blob(repo.name, repo.branch, e['config'])})"
            req_release(dep, tag, report, "release-should-be-valid").because(named + ".")
            if cfg.endswith("/prod.jsonc"):
                req_release(dep, tag, report, "release-should-be-stable").because(
                    named + ": a prod config names only stable releases.")
        for tag, version in repo.prod_tags().items():
            listed = f"On the prod list of [`{version}/channels.jsonc`]({blob(repo.name, repo.branch, repo.channels_path(version))})"
            req_release(repo, tag, report, "release-should-be-stable").because(listed + ": a prod release must be stable.")
            req_release(repo, tag, report, "release-should-be-valid").because(listed + ".")
        latest = repo.latest()
        if latest:
            req_release(repo, latest, report, "release-should-be-valid").because(
                "Marked Latest: the release people are told to download.")
    for inst in list(report.instances.values()):
        if inst.passed is not None or inst.req not in ("release-should-be-valid", "release-should-be-stable"):
            continue
        repo = repos[inst.repo]
        r = repo.release(inst.fields["release_tag"])
        if r is None:
            inst.verdict(False, "The release is missing.")
        elif inst.req == "release-should-be-stable":
            inst.verdict(not r["prerelease"], "The release is stable." if not r["prerelease"]
                         else "The release is a pre-release: mark it stable on its page.")
        else:
            assets = {a["name"] for a in r["assets"]}
            missing = [f"`{f}`" for f in ("SHA256SUMS", "BUILD-INFO.json") if f not in assets]
            extra = sorted(set(sums(repo.name, r["tag_name"]) or {}) - assets)
            problems = ([f"It has no {' or '.join(missing)}."] if missing else []) + \
                       [f"Its `SHA256SUMS` lists `{f}`, which it doesn't have." for f in extra]
            inst.verdict(not problems, " ".join(problems) or
                         "It has `SHA256SUMS`, `BUILD-INFO.json` and every file `SHA256SUMS` lists.")


def release_channel(repo: Repo, tag: str) -> str | None:
    """The channel of a release: from the channels.jsonc of the version
    folder its BUILD-INFO.json names (dev when unlisted); None when unknown."""
    text = release_file(repo.name, tag, "BUILD-INFO.json")
    try:
        version = (json.loads(text) if text else {}).get("config-version")
    except ValueError:
        return None
    chans = repo.channels().get(version) if version else None
    if chans is None:
        return None
    return next((c for c, tags in chans.items() if tag in tags), "dev")


def reachable_releases(repos: dict[str, Repo]) -> dict[str, dict[str, list[str]]]:
    """The dependency releases the recommended installs use: from each
    installable repo's Latest, the releases its prod.jsonc names (in the
    version folder its BUILD-INFO.json names), directly or through another
    installable it names. Dependency repo -> tag -> how it's reached."""
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
        path = f"remote-config/{product}/{version}/prod.jsonc"
        if not version or path not in repo.files:
            return
        config = json.loads(strip_comments(raw(repo.name, path, repo.branch)))
        for name, dep in config.items():
            if name == "$schema" or not isinstance(dep, dict) or dep.get("repo") not in repos:
                continue
            target, release = repos[dep["repo"]], dep.get("release", "")
            step = f"{via} → [`{version}/prod.jsonc`]({blob(repo.name, repo.branch, path)}) → {name} `{release}`"
            if target.type == "dependency":
                reach.setdefault(target.name, {}).setdefault(release, []).append(step)
            elif target.type == "installable":
                walk(target, release, step)

    for repo in sorted(repos.values(), key=lambda r: r.name):
        if repo.type == "installable" and repo.latest():
            walk(repo, repo.latest(), f"{repo.short} [`{repo.latest()}`]({release_page(repo.name, repo.latest())}) (Latest)")
    return reach


def check_latest(repo: Repo, reach: dict, report: Report) -> None:
    """latest-should-be-prod and release-should-be-latest (installable);
    release-should-be-latest and latest-should-be-used (dependency). The
    rules are the website repo's CLAUDE.md, Repo types."""
    if not repo.releases() or repo.type not in ("installable", "dependency"):
        return
    latest = repo.latest()
    if repo.type == "installable":
        prod = repo.prod_tags()
        if latest is None:
            inst = report.require(repo.name, "latest-should-be-prod", "(none)", "no release is Latest", "no release is Latest")
            inst.because("People are told to install the release marked Latest; it must be a prod release once there is one.")
            inst.link("the releases", releases_page(repo.name))
            inst.verdict(not prod, "No release is prod yet, so none can be Latest." if not prod
                         else f"Prod releases exist ({', '.join(f'`{t}`' for t in sorted(prod))}): mark the newest one Latest.")
            return
        inst = req_release(repo, latest, report, "latest-should-be-prod")
        inst.because("Marked Latest: the release people are told to install, which must be a prod release.")
        channel = release_channel(repo, latest)
        if channel == "prod":
            inst.verdict(True, "It's on the prod list of its version's `channels.jsonc`.")
        else:
            inst.verdict(False, f"It's on {channel}: promote it, or mark a prod release Latest." if channel
                         else "Its channel can't be told: no `config-version` in its `BUILD-INFO.json`, or no `channels.jsonc`.")
        if prod:
            newest = max(prod)
            rel = req_release(repo, newest, report, "release-should-be-latest")
            rel.because("The newest prod release: the one people should install.")
            rel.verdict(latest == newest, "The release is Latest." if latest == newest
                        else f"Latest is `{latest}`: mark this one Latest.")
        return
    tags = {r["tag_name"] for r in repo.releases()}
    uses = {t: v for t, v in reach.get(repo.name, {}).items() if t in tags}
    if not uses:
        if latest:
            inst = req_release(repo, latest, report, "latest-should-be-used")
            inst.because("Marked Latest. A dependency's Latest is the newest release the recommended installs "
                         "(the installables' Latest) use.")
            inst.verdict(False, "No recommended install uses a release of this dependency, "
                                "so Latest sends people to a release no product uses.")
        return
    target = max(uses)
    inst = req_release(repo, target, report, "release-should-be-latest")
    for path in uses[target]:
        inst.because(f"Used by a recommended install: {path}.")
    others = [f"`{t}`" for t in sorted(uses) if t != target]
    if others:
        inst.because(f"The newest of the releases the recommended installs use (also {', '.join(others)}).")
    rel = repo.release(target)
    if latest == target:
        inst.verdict(True, "The release is Latest.")
    elif rel is not None and rel["prerelease"]:
        inst.verdict(True, "It's a pre-release, so it can't be Latest yet.")
    else:
        inst.verdict(False, f"{'Latest is `' + latest + '`' if latest else 'No release is Latest'}: mark this one Latest.")


def check_schemas(repos: dict[str, Repo], report: Report) -> None:
    """schema-copy-should-match: the website's schema copies equal their
    owners' files."""
    site = repos.get(f"{ORG}/{WEBSITE}")
    if site is None:
        return
    for path in (p for p in site.files if p.startswith("docs/schemas/") and p.endswith(".json")):
        name = path.rsplit("/", 1)[1]
        owner = next((repos[f"{ORG}/{o}"] for o in SCHEMA_OWNERS
                      if f"{ORG}/{o}" in repos and f"schemas/{name}" in repos[f"{ORG}/{o}"].files), None)
        copy = blob(site.name, site.branch, path)
        inst = report.require(site.name, "schema-copy-should-match", path, f"[`{path}`]({copy})", path, file=path)
        inst.because("Files outside the site name it by its www.openlogix.org URL.").link("the copy", copy)
        if owner is None:
            inst.verdict(False, f"No owner has `schemas/{name}` ({', '.join(SCHEMA_OWNERS)}).")
            continue
        original = blob(owner.name, owner.branch, f"schemas/{name}")
        inst.because(f"It's a copy of {owner.short}'s [`schemas/{name}`]({original}).").link("the owner's file", original)
        same = raw(site.name, path, site.branch) == raw(owner.name, f"schemas/{name}", owner.branch)
        inst.verdict(same, "Equal to its owner's file." if same else "It differs from its owner's file: copy it again.")


def check_runs(repo: Repo, report: Report) -> None:
    """daily-build-should-pass and daily-build-should-be-recent: each
    scheduled workflow's latest scheduled run on the default branch."""
    for path in (p for p in repo.files if WORKFLOW.match(p)):
        file = path.rsplit("/", 1)[1]
        if repo.short == ".github" and file.startswith("repos-janitor."):
            continue  # Itself: a failure here would only repeat.
        if not re.search(r"^\s*schedule:", raw(repo.name, path, repo.branch), re.M):
            continue
        runs = api(f"repos/{repo.name}/actions/workflows/{file}/runs?branch={repo.branch}&event=schedule&status=completed&per_page=1")
        run = runs["workflow_runs"][0] if runs and runs.get("workflow_runs") else None
        wf = f"https://github.com/{repo.name}/actions/workflows/{file}"
        why = "It runs daily, to catch breakage even without a push; the repo's status badge shows it."
        ok = report.require(repo.name, "daily-build-should-pass", file, f"[`{file}`]({wf})", file, workflow=file)
        recent = report.require(repo.name, "daily-build-should-be-recent", file, f"[`{file}`]({wf})", file, workflow=file)
        for inst in (ok, recent):
            inst.because(why).link("the workflow", wf)
        if run is None:
            recent.verdict(False, "It has no completed scheduled run yet.")
            continue
        ok.link("the run", run["html_url"])
        recent.link("the run", run["html_url"])
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        when = started.strftime("%Y-%m-%d %H:%M UTC")
        ok.verdict(run["conclusion"] == "success",
                   f"The last scheduled run ({when}) passed." if run["conclusion"] == "success"
                   else f"The last scheduled run ({when}) ended: {run['conclusion']}.")
        age = now() - started
        recent.verdict(age <= RUN_MAX_AGE, f"The last scheduled run was {age.days} day(s) ago ({when}).")


def check_tags(repo: Repo, report: Report) -> None:
    """release-tag-should-be-a-date: every release of a repo with releases."""
    if not repo.all_releases():
        return
    page = releases_page(repo.name)
    inst = report.require(repo.name, "release-tag-should-be-a-date", repo.name, f"[{repo.name} releases]({page})",
                          f"{repo.name} releases")
    inst.because("Every Open Logix release is tagged with its date, `yyyy-mm-dd` "
                 "(the website repo's CLAUDE.md, Versions and tags); the other checks see only those.")
    inst.link("the releases", page)
    bad = [r["tag_name"] for r in repo.all_releases() if not DATE_TAG.match(r["tag_name"])]
    for tag in bad:
        inst.link(f"`{tag}`", release_page(repo.name, tag))
    inst.verdict(not bad, f"All {plural(len(repo.all_releases()), 'release')} are date-tagged." if not bad
                 else f"Not date-tagged: {', '.join(f'`{t}`' for t in bad)}. Delete them, or publish them again under their date.")


def check_type(repo: Repo, report: Report) -> None:
    """repo-should-have-one-type and repo-should-have-no-releases."""
    page = f"https://github.com/{repo.name}"
    inst = report.require(repo.name, "repo-should-have-one-type", repo.name, f"[{repo.name}]({page})", repo.name)
    inst.because("The repo type (its topic) decides its release policy (the website repo's CLAUDE.md, Repo types).")
    inst.link("the repo, to set its topic", page)
    if not repo.topics:
        inst.verdict(False, "It has no type topic: add `openlogix-installable`, `openlogix-dependency` "
                            "or `openlogix-no-releases`.")
        return
    if len(repo.topics) > 1:
        inst.verdict(False, f"It has several: {', '.join(f'`{t}`' for t in repo.topics)}. Keep one.")
        return
    inst.verdict(True, f"It's {repo.type} (topic `{repo.topics[0]}`).")
    if repo.type == "no releases":
        nr = report.require(repo.name, "repo-should-have-no-releases", repo.name, f"[{repo.name}]({page})", repo.name)
        nr.because("Its type is \"no releases\": it's read, never downloaded.").link("the releases", releases_page(repo.name))
        tags = [r["tag_name"] for r in repo.all_releases()]
        nr.verdict(not tags, "It has no releases." if not tags
                   else f"It has releases: {', '.join(f'`{t}`' for t in tags)}. Delete them, or change the type.")


# ---------------------------------------------------------------- output

def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def plain(text: str) -> str:
    """Markdown to plain text: links to their text, no backticks."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).replace("`", "")


def ordered(instances: list[Instance]) -> list[Instance]:
    return sorted(instances, key=lambda i: (i.repo, list(REQUIREMENTS).index(i.req), i.subject))


def write_summary(report: Report, checked: int, path: str, results_json: str) -> None:
    """The run's summary page: one status line, then the errors by
    repo and requirement, each subject with its [Why], [Result] and [Fix]
    links; then what passed, folded per repo; then the JSON results."""
    errors, passed = report.failed(), report.passed()
    mark = "❌" if errors else "✅"
    lines = [f"{mark}&nbsp;&nbsp; **{plural(len(errors), 'error')}, "
             f"{len(passed)} passed** · {checked} repos · {now().strftime('%Y-%m-%d %H:%M')} UTC"]

    def block(instances: list[Instance]) -> list[str]:
        out = []
        for req in sorted({i.req for i in instances}, key=list(REQUIREMENTS).index):
            out.append(f"- {REQUIREMENTS[req]}")
            for i in ordered([i for i in instances if i.req == req]):
                out.append(f"  - {i.subject_md}")
                out += [f"    - [Why] {w}" for w in i.why]
                out += [f"    - [Result] {r}" for r in i.result]
                if i.links:
                    out.append(f"    - [{'See' if i.passed else 'Fix'}] "
                               + " · ".join(f"[{label}]({url})" for label, url in i.links))
        return out

    if errors:
        lines += ["", "<br>", "", "### Errors"]
        for repo in sorted({i.repo for i in errors}):
            lines += ["", f"**{repo}**"] + block([i for i in errors if i.repo == repo])
    lines += ["", "<br>", "", "### Passed"]
    for repo in sorted({i.repo for i in passed}):
        mine = [i for i in passed if i.repo == repo]
        lines += ["", f"<details><summary><b>{repo}</b> · {len(mine)} passed</summary>", ""] + block(mine) + ["", "</details>"]
    lines += ["", "<br>", "", "### Verification results", "", "<details><summary>JSON</summary>", "",
              "```json", results_json, "```", "", "</details>"]
    Path(path).write_text("\n".join(lines) + "\n")


def results(report: Report) -> dict:
    """The results for tools, as Apio's: by repo, then requirement type."""
    def section(instances):
        out: dict = {}
        for i in ordered(instances):
            entry = {**i.fields, "subject": plain(i.subject),
                     "notes": [f"[Why] {plain(w)}" for w in i.why] + [f"[Result] {plain(r)}" for r in i.result],
                     "links": dict(i.links)}
            out.setdefault(i.repo, {}).setdefault(i.req, []).append(entry)
        return out
    return {"passed": not report.failed(), "failures": section(report.failed()),
            "successes": section(report.passed())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--summary", required=True, help="the Markdown report (the workflow adds it to the run's summary)")
    ap.add_argument("--results", required=True, help="the results as JSON")
    args = ap.parse_args()

    infos = [i for i in api(f"orgs/{ORG}/repos?type=public&per_page=100") or [] if not i["archived"]]
    repos = {i["full_name"]: Repo(i) for i in infos}
    report = Report()
    entries_by_repo = {}
    for repo in sorted(repos.values(), key=lambda r: r.name):
        check_type(repo, report)
        check_tags(repo, report)
        entries_by_repo[repo.name] = check_configs(repo, report)
        check_pins(repo, entries_by_repo[repo.name], repos, report)
        check_runs(repo, report)
    check_releases(repos, entries_by_repo, report)
    reach = reachable_releases(repos)
    for repo in sorted(repos.values(), key=lambda r: r.name):
        check_latest(repo, reach, report)
    check_schemas(repos, report)

    res = json.dumps(results(report), indent=2)
    Path(args.results).write_text(res + "\n")
    for i in ordered(report.failed()):
        print(f"::error title={REQUIREMENTS[i.req]} ({i.repo})::{plain(i.subject)}: {plain(' '.join(i.result))}")
    print(f"repos-janitor: {len(repos)} repos, {plural(len(report.failed()), 'error')}, {len(report.passed())} passed.")
    write_summary(report, len(repos), args.summary, res)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
