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
`gh release download`) and each repo's public page (its About sections),
read-only. Writes --summary (Markdown, for the run's
summary page) and --results (JSON, for tools); prints a GitHub annotation
per failed instance. The exit status is 1 if any requirement failed.

For tests: JANITOR_NOW (an ISO time) sets the current time, and JANITOR_PAGES
a folder of <repo>.html files in place of the repos' pages on github.com.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
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
# Repo types (the website repo's CLAUDE.md, Repo types), from each repo's
# metadata file.
METADATA = ".github/logix-repo-metadata.jsonc"
METADATA_SCHEMA = "https://www.openlogix.org/schemas/logix-repo-metadata-v1.json"
TYPES = ["installable", "dependency", "no-releases"]
# Where the release model's rules are (Remote updates, Repo types).
POLICY_URL = "https://github.com/openlogix/openlogix.github.io/blob/main/CLAUDE.md"
DATE_TAG = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# Repo setup (the website repo's CLAUDE.md, README header and Status).
SITE = "https://www.openlogix.org"
STANDARD_FILES = ["README.md", "LICENSE-MIT", "LICENSE-APACHE"]
# A placeholder repo (a product not started yet) holds only these, and no
# CLAUDE.md.
PLACEHOLDER_FILES = set(STANDARD_FILES) | {METADATA}
# Each platform's icon in README headers.
PLATFORM_ICONS = {"darwin-arm64": f"{SITE}/brand/readme/macos.svg"}
LICENSE_BADGE = "[![License: MIT or Apache 2.0](https://img.shields.io/badge/License-MIT%20or%20Apache%202.0-blue)]("
STATUS_BADGE = re.compile(r"\[!\[([^\]]*)\]\((https://img\.shields\.io/github/actions/workflow/status/"
                          r"([^/]+/[^/]+)/([^?)]+)\??([^)]*))\)\]\(([^)]*)\)")
BANNER = re.compile(r'^<picture>\s*<source media="\(prefers-color-scheme: dark\)" '
                    r'srcset="' + re.escape(SITE) + r'/brand/readme/([a-z-]+)-dark\.svg">\s*'
                    r'<img alt="[^"]+" src="' + re.escape(SITE) + r'/brand/readme/([a-z-]+)-light\.svg" '
                    r'height="96">\s*</picture>\s*<br>')
# A workflow's triggers that make it run on its own (else it's reusable).
OWN_TRIGGERS = re.compile(r"\b(push|pull_request|pull_request_target|schedule|workflow_dispatch|"
                          r"workflow_run|repository_dispatch|release)\b")

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
    "repo-metadata-should-be-valid": "Repo metadata should be valid",
    "repo-should-have-no-releases": "Repo should have no releases",
    "repo-settings-should-match": "Repo settings should match",
    "repo-sidebar-should-match": "Repo sidebar should match its type",
    "about-should-be-set": "About should be set",
    "repo-should-have-standard-files": "Repo should have its standard files",
    "readme-header-should-be-standard": "README header should be standard",
    "readme-badges-should-match-workflows": "README badges should match the workflows",
    "org-lists-should-include-repo": "Org lists should include the repo",
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


_raw: dict = {}


def raw(repo: str, path: str, ref: str) -> str:
    """A file's text on a branch (read once)."""
    if (repo, path, ref) not in _raw:
        _raw[(repo, path, ref)] = _read_raw(repo, path, ref)
    return _raw[(repo, path, ref)]


def _read_raw(repo: str, path: str, ref: str) -> str:
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


def repo_page(repo: str) -> str | None:
    """The repo's public page on github.com (HTML); None if it can't be
    read. For tests, JANITOR_PAGES is a folder of <repo name>.html files."""
    pages = os.environ.get("JANITOR_PAGES")
    if pages:
        f = Path(pages) / f"{repo.split('/')[1]}.html"
        return f.read_text() if f.is_file() else None
    try:
        req = urllib.request.Request(f"https://github.com/{repo}", headers={"User-Agent": "repos-janitor"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except OSError:
        return None


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
        self.info = info
        self.name = info["full_name"]
        self.short = info["name"]
        self.branch = info["default_branch"]
        tree = api(f"repos/{self.name}/git/trees/{self.branch}?recursive=1") or {}
        self.files = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
        self._releases = None
        self._latest = "unread"
        self._channels = None
        # The metadata file: its problem (None when it's valid), the type and
        # where its issues go.
        self.issues = None
        self.type, self.metadata_problem = self.read_metadata()

    def read_metadata(self) -> tuple[str | None, str | None]:
        if METADATA not in self.files:
            return None, "Missing."
        try:
            data = json.loads(strip_comments(raw(self.name, METADATA, self.branch)))
        except ValueError as e:
            return None, f"Not valid JSONC: {e}"
        if not isinstance(data, dict) or data.get("$schema") != METADATA_SCHEMA:
            return None, f"`$schema` isn't `{METADATA_SCHEMA}`."
        extra = sorted(set(data) - {"$schema", "type", "issues"})
        if extra:
            return None, f"Unknown keys: {', '.join(f'`{k}`' for k in extra)}."
        if data.get("type") not in TYPES:
            return None, f"Unknown type `{data.get('type')}`."
        issues = data.get("issues")
        if not isinstance(issues, str) or not re.match(r"^(here|openlogix/[A-Za-z0-9._-]+)$", issues):
            return None, f"Its `issues` must be `here` or a repo such as `openlogix/logix-engine`, not `{issues}`."
        self.issues = issues
        return data["type"], None

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

    def readme(self) -> str | None:
        return raw(self.name, "README.md", self.branch) if "README.md" in self.files else None

    def is_placeholder(self) -> bool:
        return set(self.files) <= PLACEHOLDER_FILES

    def workflows(self) -> list[str]:
        """The workflow files that run on their own (not only reusable ones
        another workflow calls), by file name."""
        out = []
        for path in (p for p in self.files if WORKFLOW.match(p)):
            text = raw(self.name, path, self.branch)
            m = re.search(r"^on:(.*?)(?=^\S|\Z)", text, re.M | re.S)
            if m is None or OWN_TRIGGERS.search(m.group(1)) or "workflow_call" not in m.group(1):
                out.append(path.rsplit("/", 1)[1])
        return sorted(out)


def req_release(repo: Repo, tag: str, report: Report, req: str) -> Instance:
    return report.require(repo.name, req, tag, f"[`{tag}`]({release_page(repo.name, tag)})", tag,
                          release_tag=tag).link("release", release_page(repo.name, tag))


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
            why = f"{product}'s release channels for config {version}."
        elif CHANNEL_CONFIG.match(p):
            why = f"Read by {product} releases on config {version}, channel {name[: -len('.jsonc')]}."
        else:
            why = f"In {product}'s remote config folder."
        url = blob(repo.name, repo.branch, p)
        report.require(repo.name, "config-should-be-valid", p, f"[`{config_name(p)}`]({url})",
                       config_name(p), file=p).because(why).link("file", url)
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
            inst.verdict(False, "Folder problems: " + " ".join(problems[""]))
        else:
            inst.verdict(True, "Valid.")
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
            inst.because(f"A dev config next to [`{prod_cfg}`]({prod_url}).")
            inst.link("dev config", cfg_url).link("prod config", prod_url)
            if tag < prod["release"]:
                inst.verdict(False, f"Older than prod's `{prod['release']}`.")
            else:
                inst.verdict(True, "Same as prod's." if tag == prod["release"]
                             else f"Newer than prod's `{prod['release']}`.")


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
                    named + ".")
        for tag, version in repo.prod_tags().items():
            listed = f"On the prod list of [`{version}/channels.jsonc`]({blob(repo.name, repo.branch, repo.channels_path(version))})"
            req_release(repo, tag, report, "release-should-be-stable").because(listed + ".")
            req_release(repo, tag, report, "release-should-be-valid").because(listed + ".")
        latest = repo.latest()
        if latest:
            req_release(repo, latest, report, "release-should-be-valid").because(
                "Marked Latest.")
    for inst in list(report.instances.values()):
        if inst.passed is not None or inst.req not in ("release-should-be-valid", "release-should-be-stable"):
            continue
        repo = repos[inst.repo]
        r = repo.release(inst.fields["release_tag"])
        if r is None:
            inst.verdict(False, "Missing.")
        elif inst.req == "release-should-be-stable":
            inst.verdict(not r["prerelease"], "Stable." if not r["prerelease"] else "A pre-release.")
        else:
            assets = {a["name"] for a in r["assets"]}
            missing = [f"`{f}`" for f in ("SHA256SUMS", "BUILD-INFO.json") if f not in assets]
            extra = sorted(set(sums(repo.name, r["tag_name"]) or {}) - assets)
            problems = ([f"No {' or '.join(missing)}."] if missing else []) + \
                       [f"`SHA256SUMS` lists `{f}`, which isn't there." for f in extra]
            inst.verdict(not problems, " ".join(problems) or "Complete.")


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
            inst.because("An installable repo with releases.")
            inst.link("releases", releases_page(repo.name))
            inst.verdict(not prod, "No prod release yet." if not prod
                         else f"No Latest, but prod releases exist: {', '.join(f'`{t}`' for t in sorted(prod))}.")
            return
        inst = req_release(repo, latest, report, "latest-should-be-prod")
        inst.because("Marked Latest.")
        channel = release_channel(repo, latest)
        if channel == "prod":
            inst.verdict(True, "Prod.")
        else:
            inst.verdict(False, f"On {channel}, not prod." if channel
                         else "Channel unknown: no `config-version` in `BUILD-INFO.json`, or no `channels.jsonc`.")
        if prod:
            newest = max(prod)
            rel = req_release(repo, newest, report, "release-should-be-latest")
            rel.because("The newest prod release.")
            rel.verdict(latest == newest, "Latest." if latest == newest else f"Not Latest (Latest is `{latest}`).")
        return
    tags = {r["tag_name"] for r in repo.releases()}
    uses = {t: v for t, v in reach.get(repo.name, {}).items() if t in tags}
    if not uses:
        if latest:
            inst = req_release(repo, latest, report, "latest-should-be-used")
            inst.because("Marked Latest.")
            inst.verdict(False, "Not used by a recommended install.")
        return
    target = max(uses)
    inst = req_release(repo, target, report, "release-should-be-latest")
    for path in uses[target]:
        inst.because(f"Used by a recommended install: {path}.")
    others = [f"`{t}`" for t in sorted(uses) if t != target]
    if others:
        inst.because(f"Newer than the others recommended installs use ({', '.join(others)}).")
    rel = repo.release(target)
    if latest == target:
        inst.verdict(True, "Latest.")
    elif rel is not None and rel["prerelease"]:
        inst.verdict(True, "A pre-release: can't be Latest yet.")
    else:
        inst.verdict(False, f"Not Latest ({'Latest is `' + latest + '`' if latest else 'no Latest'}).")


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
        inst.because("A schema copy on the website.").link("copy", copy)
        if owner is None:
            inst.verdict(False, f"No owner file `schemas/{name}`.")
            continue
        original = blob(owner.name, owner.branch, f"schemas/{name}")
        inst.because(f"Its owner: {owner.short} [`schemas/{name}`]({original}).").link("owner's file", original)
        same = raw(site.name, path, site.branch) == raw(owner.name, f"schemas/{name}", owner.branch)
        inst.verdict(same, "Matches." if same else "Differs.")


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
        why = "A scheduled workflow."
        ok = report.require(repo.name, "daily-build-should-pass", file, f"[`{file}`]({wf})", file, workflow=file)
        recent = report.require(repo.name, "daily-build-should-be-recent", file, f"[`{file}`]({wf})", file, workflow=file)
        for inst in (ok, recent):
            inst.because(why).link("workflow", wf)
        if run is None:
            recent.verdict(False, "No scheduled run yet.")
            continue
        ok.link("run", run["html_url"])
        recent.link("run", run["html_url"])
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        when = started.strftime("%Y-%m-%d %H:%M UTC")
        ok.verdict(run["conclusion"] == "success",
                   f"Passed ({when})." if run["conclusion"] == "success"
                   else f"{run['conclusion'].capitalize()} ({when}).")
        age = now() - started
        recent.verdict(age <= RUN_MAX_AGE, f"Last run {age.days} day(s) ago ({when}).")


def check_tags(repo: Repo, report: Report) -> None:
    """release-tag-should-be-a-date: every release of a repo with releases."""
    if not repo.all_releases():
        return
    page = releases_page(repo.name)
    inst = report.require(repo.name, "release-tag-should-be-a-date", repo.name, f"[{repo.name} releases]({page})",
                          f"{repo.name} releases")
    inst.because("An openlogix repo with releases.")
    inst.link("releases", page)
    bad = [r["tag_name"] for r in repo.all_releases() if not DATE_TAG.match(r["tag_name"])]
    for tag in bad:
        inst.link(f"`{tag}`", release_page(repo.name, tag))
    inst.verdict(not bad, "All date-tagged." if not bad
                 else f"Not date-tagged: {', '.join(f'`{t}`' for t in bad)}.")


def check_type(repo: Repo, report: Report) -> None:
    """repo-metadata-should-be-valid and repo-should-have-no-releases."""
    page = f"https://github.com/{repo.name}"
    file_url = blob(repo.name, repo.branch, METADATA)
    inst = report.require(repo.name, "repo-metadata-should-be-valid", repo.name,
                          f"[`{METADATA}`]({file_url})", METADATA, file=METADATA)
    inst.because("A repo of the openlogix org.")
    if METADATA in repo.files:
        inst.link("file", file_url)
    else:
        inst.link("create it", f"https://github.com/{repo.name}/new/{repo.branch}?filename={METADATA}")
    if repo.metadata_problem:
        inst.verdict(False, repo.metadata_problem)
        return
    inst.verdict(True, f"Type `{repo.type}`.")
    if repo.type == "no-releases":
        nr = report.require(repo.name, "repo-should-have-no-releases", repo.name, f"[{repo.name}]({page})", repo.name)
        nr.because(f"Its type is `no-releases` ([`{METADATA}`]({file_url})).").link("releases", releases_page(repo.name))
        tags = [r["tag_name"] for r in repo.all_releases()]
        nr.verdict(not tags, "No releases." if not tags
                   else f"Has releases: {', '.join(f'`{t}`' for t in tags)}.")


def check_settings(repo: Repo, repos: dict[str, Repo], report: Report) -> None:
    """repo-settings-should-match: the GitHub features users see follow the
    org rule (the website repo's CLAUDE.md, Contact and GitHub features):
    issues only where the metadata says `here`, else a README line sending
    people to the repo that takes them; no wiki, Discussions or Projects;
    private vulnerability reporting on."""
    if repo.issues is None:
        return  # repo-metadata-should-be-valid reports it.
    inst = report.require(repo.name, "repo-settings-should-match", repo.name,
                          f"[{repo.name}](https://github.com/{repo.name})", repo.name)
    inst.because("A repo of the openlogix org.").link("settings", f"https://github.com/{repo.name}/settings")
    wrong = []
    takes = repo.issues == "here"
    if bool(repo.info.get("has_issues")) != takes:
        wrong.append("issues should be " + ("on" if takes else "off"))
    for key, name in (("has_wiki", "wiki"), ("has_discussions", "Discussions"), ("has_projects", "Projects")):
        if repo.info.get(key):
            wrong.append(f"{name} should be off")
    vuln = api(f"repos/{repo.name}/private-vulnerability-reporting") or {}
    if not vuln.get("enabled"):
        wrong.append("private vulnerability reporting should be on")
    if not takes:
        target = repos.get(repo.issues)
        if target is None or target.issues != "here":
            wrong.append(f"`{repo.issues}` doesn't take issues")
        readme = raw(repo.name, "README.md", repo.branch) if "README.md" in repo.files else ""
        if f"https://github.com/{repo.issues}/issues" not in readme:
            wrong.append(f"its README should link `{repo.issues}`'s issues")
            inst.link("README", blob(repo.name, repo.branch, "README.md"))
    inst.verdict(not wrong, "Matches." if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


def product_page(repo: Repo, site: Repo | None) -> str | None:
    """The product page of a logix-<short> repo (www.openlogix.org/<short>/),
    when the website has one."""
    if site is None or not repo.short.startswith("logix-"):
        return None
    short = repo.short[len("logix-"):]
    return f"{SITE}/{short}/" if f"docs/{short}/index.html" in site.files else None


def check_sidebar(repo: Repo, report: Report) -> None:
    """repo-sidebar-should-match: the About box's sections (the gear on the
    repo page) follow the repo type: Releases only where there are releases,
    never Packages or Deployments. GitHub's API can't read them, so this reads
    them from the repo's public page, which embeds them as JSON."""
    if repo.type is None:
        return  # repo-metadata-should-be-valid reports it.
    page = f"https://github.com/{repo.name}"
    inst = report.require(repo.name, "repo-sidebar-should-match", repo.name, f"[{repo.name}]({page})", repo.name)
    inst.because(f"Its type is `{repo.type}`.").link("repo page (the About gear)", page)
    html = repo_page(repo.name) or ""
    at = html.find('"sections":{')
    try:
        sections = json.JSONDecoder().raw_decode(html, at + len('"sections":'))[0] if at >= 0 else None
    except ValueError:
        sections = None
    if not isinstance(sections, dict) or not {"releases", "packages", "deployments"} <= set(sections):
        inst.verdict(False, "Can't read its sections from the repo page: GitHub may have changed the page; "
                     "update `repos-janitor/janitor.py`.")
        return
    want = {"Releases": repo.type != "no-releases", "Packages": False, "Deployments": False}
    wrong = [f"{name} should be {'shown' if on else 'hidden'}"
             for name, on in want.items() if bool(sections[name.lower()]) != on]
    inst.verdict(not wrong, "Matches." if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


def check_about(repo: Repo, site: Repo | None, report: Report) -> None:
    """about-should-be-set: a description, and the Website field set to the
    product page (or, for a repo with none, a page of the website)."""
    page = f"https://github.com/{repo.name}"
    inst = report.require(repo.name, "about-should-be-set", repo.name, f"[{repo.name}]({page})", repo.name)
    inst.because("A repo of the openlogix org.").link("repo page (the About gear)", page)
    wrong = []
    if not (repo.info.get("description") or "").strip():
        wrong.append("no description")
    home = repo.info.get("homepage") or ""
    want = product_page(repo, site)
    if want and home != want:
        wrong.append(f"its website should be {want}")
    elif not want and not home.startswith(SITE):
        wrong.append(f"its website should be a page of {SITE}")
    inst.verdict(not wrong, "Set." if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


def check_files(repo: Repo, report: Report) -> None:
    """repo-should-have-standard-files: README, both licenses and, except in
    a placeholder, CLAUDE.md (its AI spec)."""
    page = f"https://github.com/{repo.name}"
    inst = report.require(repo.name, "repo-should-have-standard-files", repo.name, f"[{repo.name}]({page})", repo.name)
    inst.because("A repo of the openlogix org.").link("files", page)
    want = STANDARD_FILES + ([] if repo.is_placeholder() else ["CLAUDE.md"])
    missing = [f for f in want if f not in repo.files]
    inst.verdict(not missing, "All there." if not missing
                 else f"Missing: {', '.join(f'`{f}`' for f in missing)}.")


def check_readme_header(repo: Repo, site: Repo | None, report: Report) -> None:
    """readme-header-should-be-standard: the banner, the license badge, the
    platform icons (installables), the status badges and the entry-point
    links, in that order, before the first section."""
    text = repo.readme()
    if text is None:
        return  # repo-should-have-standard-files reports it.
    url = blob(repo.name, repo.branch, "README.md")
    inst = report.require(repo.name, "readme-header-should-be-standard", "README.md", f"[`README.md`]({url})",
                          "README.md", file="README.md")
    inst.because("A repo of the openlogix org.").link("README", url).link(
        "the rule", POLICY_URL + "#product-pages")
    head = text.split("\n## ", 1)[0]
    wrong = []
    banner = BANNER.match(head.lstrip())
    if banner is None or banner.group(1) != banner.group(2):
        wrong.append("no banner (light and dark) followed by `<br>`")
    order = []
    lic = head.find(LICENSE_BADGE)
    if lic < 0:
        wrong.append("no license badge")
    order.append(("license badge", lic))
    if repo.type == "installable":
        for platform in PLATFORMS:
            at = head.find(f'<img src="{PLATFORM_ICONS[platform]}" height="40"')
            if at < 0:
                wrong.append(f"no {platform} icon")
            order.append((f"{platform} icon", at))
    badge = STATUS_BADGE.search(head)
    order.append(("status badges", badge.start() if badge else -1))
    product = product_page(repo, site)
    if product and not repo.is_placeholder():
        links = f"**[Main page]({product})**"
        if ".github/workflows/publish-docs.yaml" in repo.files:
            links += f" · [Docs]({SITE}/{repo.short}/)"
        at = head.find(links)
        if at < 0:
            wrong.append(f"no entry-point links (`{links} · ...`)")
        order.append(("entry-point links", at))
    elif product and product not in head:
        wrong.append(f"no link to its main page ({product})")
    found = [(name, at) for name, at in order if at >= 0]
    for (a, x), (b, y) in zip(found, found[1:]):
        if x > y:
            wrong.append(f"the {a} should come before the {b}")
    inst.verdict(not wrong, "Standard." if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


def check_badges(repo: Repo, report: Report) -> None:
    """readme-badges-should-match-workflows: one status badge per workflow
    that runs on its own, showing its last run on main (`branch=main` its only
    filter), labeled as its alt text, and none for a workflow that isn't
    there. GitHub's own workflows (pages/...) aren't files and are skipped."""
    text = repo.readme()
    if text is None:
        return
    url = blob(repo.name, repo.branch, "README.md")
    inst = report.require(repo.name, "readme-badges-should-match-workflows", "README.md",
                          f"[`README.md`]({url})", "README.md", file="README.md")
    inst.because("A repo of the openlogix org.").link("README", url).link(
        "workflows", f"https://github.com/{repo.name}/actions")
    workflows = repo.workflows()
    wrong = []
    seen = set()
    for m in STATUS_BADGE.finditer(text):
        label, owner, wf, query, target = m.group(1), m.group(3), m.group(4), m.group(5), m.group(6)
        if "%2F" in wf or owner != repo.name:
            continue  # GitHub's own, or another repo's (the website's Status).
        if wf not in workflows:
            wrong.append(f"a badge for `{wf}`, which isn't one of its workflows")
            continue
        seen.add(wf)
        if query != f"branch=main&label={label}":
            wrong.append(f"`{wf}`'s badge should have `?branch=main&label={label}` only")
        if target != f"https://github.com/{repo.name}/actions/workflows/{wf}":
            wrong.append(f"`{wf}`'s badge should link its runs")
    wrong += [f"no badge for `{wf}`" for wf in workflows if wf not in seen]
    inst.verdict(not wrong, (f"One per workflow ({len(workflows)})." if workflows else "No workflows.")
                 if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


def check_org_lists(repo: Repo, repos: dict[str, Repo], report: Report) -> None:
    """org-lists-should-include-repo: the org page's table of repos (in
    .github's profile/README.md) has its row, and the website README's Status
    section has a badge for each of its workflows (and none for others)."""
    page = f"https://github.com/{repo.name}"
    inst = report.require(repo.name, "org-lists-should-include-repo", repo.name, f"[{repo.name}]({page})", repo.name)
    inst.because("A repo of the openlogix org.")
    wrong = []
    shared, site = repos.get(f"{ORG}/.github"), repos.get(f"{ORG}/{WEBSITE}")
    if shared is not None:
        url = blob(shared.name, shared.branch, "profile/README.md")
        inst.link("org page table", url)
        table = raw(shared.name, "profile/README.md", shared.branch) if "profile/README.md" in shared.files else ""
        if f"[{repo.short}]({page})" not in table:
            wrong.append("not in the org page's table")
    if site is not None:
        inst.link("website Status", blob(site.name, site.branch, "README.md") + "#status")
        status = (site.readme() or "").split("\n## Status", 1)[-1].split("\n## ", 1)[0]
        listed = {m.group(4) for m in STATUS_BADGE.finditer(status) if m.group(3) == repo.name}
        workflows = repo.workflows()
        wrong += [f"no website Status badge for `{wf}`" for wf in workflows if wf not in listed]
        wrong += [f"a website Status badge for `{wf}`, which isn't one of its workflows"
                  for wf in sorted(listed) if wf not in workflows and "%2F" not in wf]
    inst.verdict(not wrong, "Listed." if not wrong else "Mismatch: " + "; ".join(wrong) + ".")


# ---------------------------------------------------------------- output

def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def plain(text: str) -> str:
    """Markdown to plain text: links to their text, no backticks."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).replace("`", "")


def ordered(instances: list[Instance]) -> list[Instance]:
    return sorted(instances, key=lambda i: (i.repo, list(REQUIREMENTS).index(i.req), i.subject))


def write_summary(report: Report, checked: int, path: str, results_json: str) -> None:
    """The run's summary page: one status line, then what failed, by
    repo and requirement, each subject with its [Why], [Result] and [Fix]
    links; then what passed, folded per repo; then the JSON results."""
    errors, passed = report.failed(), report.passed()
    mark = "❌" if errors else "✅"
    lines = [f"{mark}&nbsp;&nbsp; **{len(errors)} failed, "
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
        lines += ["", "<br>", "", "### Failed"]
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
    site = repos.get(f"{ORG}/{WEBSITE}")
    for repo in sorted(repos.values(), key=lambda r: r.name):
        check_settings(repo, repos, report)
        check_sidebar(repo, report)
        check_about(repo, site, report)
        check_files(repo, report)
        check_readme_header(repo, site, report)
        check_badges(repo, report)
        check_org_lists(repo, repos, report)
    reach = reachable_releases(repos)
    for repo in sorted(repos.values(), key=lambda r: r.name):
        check_latest(repo, reach, report)
    check_schemas(repos, report)

    res = json.dumps(results(report), indent=2)
    Path(args.results).write_text(res + "\n")
    for i in ordered(report.failed()):
        print(f"::error title={REQUIREMENTS[i.req]} ({i.repo})::{plain(i.subject)}: {plain(' '.join(i.result))}")
    print(f"repos-janitor: {len(repos)} repos, {len(report.failed())} failed, {len(report.passed())} passed.")
    write_summary(report, len(repos), args.summary, res)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
