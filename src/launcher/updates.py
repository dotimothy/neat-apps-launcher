"""Check GitHub for newer studio (or launcher) versions, by commit hash, over git.

Everything goes through plain git (fetch, ls-remote), never GitHub's REST API,
so there is no hourly request limit to run into.

* git checkout: `git fetch` the same branch into the checkout and count the
  commits HEAD is behind (or ahead of) it.
* downloaded copy (no .git): the launcher keeps a small mirror of each repo in
  .cache/mirrors (commit history and file lists only: a blob-less, shallow
  fetch). Every installed file is hashed the way git hashes blobs and compared
  with the file hashes in the branch's latest commit. An exact match means up
  to date; otherwise recent commits are tried the same way to find which one
  is installed, and so how many commits behind it is. A match is remembered in
  .neat-launcher-version so later checks start there.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

VERSION_FILE = ".neat-launcher-version"
CACHE_SECONDS = 300
CANDIDATES = 6            # recent commits tried when a downloaded copy is not the latest
DEPTH = 40                # commits of history kept in each mirror
CACHE_DIR = Path(os.environ.get("NEAT_LAUNCHER_CACHE")
                 or Path(__file__).resolve().parents[2] / ".cache") / "mirrors"

_cache: dict[str, tuple[float, object]] = {}      # commit lists, branch lists (short-lived)
_trees: dict[tuple[str, str], dict] = {}           # tree sha -> {path: (blob sha, mode)} (immutable)
_blobs: dict[tuple[str, int, int], str] = {}       # (path, mtime_ns, size) -> blob sha
_fetched: dict[tuple[str, str], float] = {}        # (repo, branch) -> last mirror fetch
_locks: dict[str, threading.Lock] = {}


class NotOnGitHub(Exception):
    pass


def _run(args: list[str], timeout: float = 90.0) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C")
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or [f"{args[1]} failed"])[-1]
        if "Repository not found" in r.stderr or "not found" in msg and "repository" in msg.lower():
            raise NotOnGitHub("the repository is not published on GitHub yet")
        if "couldn't find remote ref" in r.stderr:
            raise NotOnGitHub("that branch is not on GitHub")
        raise RuntimeError(msg.removeprefix("fatal: "))
    return r.stdout


def _url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def forget(repo: str | None = None) -> None:
    """Make the next check fetch from GitHub again (all repos, or one)."""
    for k in list(_fetched):
        if repo is None or k[0] == repo:
            del _fetched[k]
    for k in list(_cache):
        if repo is None or k.startswith(repo):
            del _cache[k]


def mirror(repo: str) -> Path:
    m = CACHE_DIR / (repo.replace("/", "__") + ".git")
    if not (m / "HEAD").exists():
        m.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q", "--bare", str(m)])
        _run(["git", "-C", str(m), "config", "remote.origin.url", _url(repo)])
        _run(["git", "-C", str(m), "config", "remote.origin.promisor", "true"])
        _run(["git", "-C", str(m), "config", "remote.origin.partialclonefilter", "blob:none"])
    return m


def fetch(repo: str, branch: str, force: bool = False) -> Path:
    """Bring the mirror's copy of a branch up to date (at most every few minutes)."""
    m = mirror(repo)
    key = (repo, branch)
    with _locks.setdefault(repo, threading.Lock()):
        if force or time.time() - _fetched.get(key, 0) > CACHE_SECONDS:
            _run(["git", "-C", str(m), "fetch", "-q", "--no-tags", "--filter=blob:none",
                  f"--depth={DEPTH}", "origin", f"+refs/heads/{branch}:refs/heads/{branch}"])
            _fetched[key] = time.time()
            _cache.pop(f"{repo}@{branch}", None)
    return m


def _commits(git_dir: Path, ref: str, n: int, repo: str) -> list[dict]:
    out = _run(["git", "-C", str(git_dir), "log", f"-n{n}", "--format=%H%x1f%T%x1f%cI%x1f%s", ref])
    commits = []
    for line in out.splitlines():
        sha, tree, date, subject = line.split("\x1f", 3)
        commits.append({"sha": sha, "tree": tree, "date": date, "message": subject[:140],
                        "url": f"https://github.com/{repo}/commit/{sha}"})
    return commits


def recent_commits(repo: str, branch: str = "main", n: int = CANDIDATES) -> list[dict]:
    """The newest commits on a branch, newest first."""
    key = f"{repo}@{branch}"
    m = fetch(repo, branch)
    hit = _cache.get(key)
    if hit:
        return hit[1]                                   # type: ignore[return-value]
    commits = _commits(m, f"refs/heads/{branch}", n, repo)
    if not commits:
        raise NotOnGitHub(f"{repo} has no commits on {branch}")
    _cache[key] = (time.time(), commits)
    return commits


def latest_commit(repo: str, branch: str = "main") -> dict:
    return recent_commits(repo, branch)[0]


def remote_tree(repo: str, tree_sha: str) -> dict:
    """{path: (blob sha, mode)} for every file in a commit's tree (from the mirror)."""
    key = (repo, tree_sha)
    if key not in _trees:
        out = _run(["git", "-C", str(mirror(repo)), "ls-tree", "-r", "-z", tree_sha])
        tree = {}
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            mode, kind, sha = meta.split()
            if kind == "blob":
                tree[path] = (sha, mode)
        _trees[key] = tree
    return _trees[key]


def commits_behind(repo: str, branch: str, sha: str) -> int | None:
    """How many commits the branch has that `sha` doesn't (None if unknown)."""
    m = mirror(repo)
    for deepen in (None, 400):
        try:
            if deepen:
                _run(["git", "-C", str(m), "fetch", "-q", "--no-tags", "--filter=blob:none",
                      f"--deepen={deepen}", "origin", f"+refs/heads/{branch}:refs/heads/{branch}"])
            _run(["git", "-C", str(m), "cat-file", "-e", f"{sha}^{{commit}}"])
            return int(_run(["git", "-C", str(m), "rev-list", "--count", f"{sha}..refs/heads/{branch}"]).strip())
        except (RuntimeError, ValueError):
            continue
    return None


# -- local hashing -------------------------------------------------------------
def blob_sha(path: Path, mode: str = "100644") -> str | None:
    """The git blob hash of a file (or a symlink's target), None if missing."""
    try:
        if mode == "120000":
            data = os.readlink(path).encode()
            return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _blobs:
        h = hashlib.sha1(b"blob %d\0" % st.st_size)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _blobs[key] = h.hexdigest()
    return _blobs[key]


def _export_ignored(d: Path) -> list[str]:
    """Patterns a GitHub download leaves out (export-ignore in .gitattributes)."""
    out = []
    try:
        for line in (d / ".gitattributes").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and "export-ignore" in parts[1:]:
                out.append(parts[0].lstrip("/"))
    except OSError:
        pass
    return out


def _ignored(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.rstrip("/")
        if fnmatch.fnmatch(path, p) or path.startswith(p + "/") or fnmatch.fnmatch(path.rsplit("/", 1)[-1], p):
            return True
    return False


def diff_against(d: Path, tree: dict) -> list[str]:
    """Files in the commit that are missing or different in the local copy."""
    skip = _export_ignored(d)
    return sorted(p for p, (sha, mode) in tree.items()
                  if not _ignored(p, skip) and blob_sha(d / p, mode) != sha)


# -- installed state -------------------------------------------------------------
def _git(d: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def git_state(d: Path) -> dict | None:
    if not (d / ".git").exists():
        return None
    sha = _git(d, "rev-parse", "HEAD")
    if not sha:
        return None
    branch = _git(d, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = _git(d, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream and "/" in upstream:
        branch = upstream.split("/", 1)[1]            # origin/feature/x -> feature/x
    dirty = bool(_git(d, "status", "--porcelain", "--untracked-files=no"))
    return {"sha": sha, "branch": branch if branch != "HEAD" else "", "dirty": dirty}


def recorded(d: Path) -> dict | None:
    try:
        data = json.loads((d / VERSION_FILE).read_text())
        return data if data.get("sha") else None
    except (OSError, ValueError):
        return None


def record_version(d: Path, info: dict, branch: str | None = None) -> None:
    """Remember the installed commit (and the branch it came from)."""
    data = {k: info[k] for k in ("sha", "date") if k in info}
    branch = branch or (recorded(d) or {}).get("branch")
    if branch:
        data["branch"] = branch
    try:
        (d / VERSION_FILE).write_text(json.dumps(data))
    except OSError:
        pass


def installed_branch(d: Path | None, default: str) -> str:
    """The branch a studio was installed from: its git branch, the branch the
    launcher recorded, or the default."""
    if d is None or not d.exists():
        return default
    g = git_state(d)
    if g and g.get("branch"):
        return g["branch"]
    return (recorded(d) or {}).get("branch") or default


def ls_remote(repo: str, timeout: float = 20.0) -> dict:
    """Branches straight from git (no API, so no rate limit):
    {'default': name, 'branches': [{'name', 'sha'}]}."""
    url = f"https://github.com/{repo}.git"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD", "refs/heads/*"],
                       capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["git ls-remote failed"])[-1])
    default, out = "main", []
    for line in r.stdout.splitlines():
        a, _, b = line.partition("\t")
        if a.startswith("ref: refs/heads/") and b == "HEAD":
            default = a[len("ref: refs/heads/"):]
        elif b.startswith("refs/heads/"):
            out.append({"name": b[len("refs/heads/"):], "sha": a})
    return {"default": default, "branches": out}


def branches(repo: str) -> dict:
    """{'default': name, 'branches': [{'name', 'sha'}]} (cached a few minutes)."""
    key = f"{repo}#branches"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]                                   # type: ignore[return-value]
    out = ls_remote(repo)
    out["branches"].sort(key=lambda b: (b["name"] != out["default"], b["name"].lower()))
    _cache[key] = (time.time(), out)
    return out


def _version_file(d: Path) -> str:
    try:
        return (d / "VERSION").read_text().strip()[:40]
    except OSError:
        return ""


# -- checks ----------------------------------------------------------------------
def git_compare(d: Path, branch: str, repo: str) -> tuple[dict, int, int]:
    """Fetch `branch` into the checkout; (latest commit, commits ahead, commits behind)."""
    _run(["git", "-C", str(d), "fetch", "-q", "--no-tags", "origin", branch], timeout=120)
    latest = _commits(d, "FETCH_HEAD", 1, repo)[0]
    counts = _run(["git", "-C", str(d), "rev-list", "--left-right", "--count", "HEAD...FETCH_HEAD"]).split()
    return latest, int(counts[0]), int(counts[1])


def _check_git(repo: str, branch: str, g: dict, d: Path) -> dict:
    want = g["branch"] or branch
    try:
        latest, ahead, behind = git_compare(d, want, repo)
    except NotOnGitHub:
        if want == branch:
            raise
        want = branch                                    # branch not on GitHub: use the default
        latest, ahead, behind = git_compare(d, want, repo)
    out = {"method": "git", "latest": latest, "branch": want, "behind_by": behind, "ahead_by": ahead,
           "installed": {"sha": g["sha"], "branch": g["branch"], "dirty": g["dirty"]}}
    status = ("up_to_date" if not behind and not ahead else "available" if not ahead
              else "ahead" if not behind else "diverged")
    return {**out, "status": status}


def _check_files(repo: str, branch: str, d: Path) -> dict:
    commits = recent_commits(repo, branch)
    latest = commits[0]
    out = {"method": "files", "latest": latest, "branch": branch, "installed": {}}
    changed = diff_against(d, remote_tree(repo, latest["tree"]))
    if not changed:
        record_version(d, latest, branch)
        return {**out, "status": "up_to_date", "behind_by": 0,
                "installed": {"sha": latest["sha"], "matched": "files"}}
    # Which commit is installed? Try the one recorded at install time, then recent ones.
    rec = recorded(d)
    candidates = list(commits[1:])
    if rec and rec["sha"] not in {c["sha"] for c in candidates}:
        try:
            m = mirror(repo)
            if commits_behind(repo, branch, rec["sha"]) is not None:
                candidates.insert(0, _commits(m, rec["sha"], 1, repo)[0])
        except Exception:                                   # noqa: BLE001
            pass
    for c in candidates:
        try:
            if not diff_against(d, remote_tree(repo, c["tree"])):
                record_version(d, c, branch)
                behind = next((i for i, x in enumerate(commits) if x["sha"] == c["sha"]), None)
                if behind is None:
                    behind = commits_behind(repo, branch, c["sha"])
                return {**out, "status": "available", "behind_by": behind,
                        "installed": {"sha": c["sha"], "matched": "files", "date": c["date"],
                                      "message": c["message"]}}
        except RuntimeError:
            continue
    # The recorded commit is the latest, yet files differ: edited here, not out of date.
    status = "modified" if rec and rec["sha"] == latest["sha"] else "available"
    return {**out, "status": status, "changed_count": len(changed),
            "changed_files": changed[:12],
            "installed": {"sha": rec["sha"] if rec else None, "matched": "none"}}


_last: dict[tuple[str, str], dict] = {}       # last good result per (repo, dir)


def check(repo: str, branch: str, d: Path | None) -> dict:
    """{'status': 'up_to_date' | 'available' | 'ahead' | 'diverged' | 'not_installed'
    | 'error', 'method', 'installed', 'latest', 'behind_by', 'ahead_by', ...}
    If GitHub can't be reached, the last good result comes back with
    'stale': True and a 'note' instead of an error."""
    key = (repo, str(d))
    res = _check(repo, branch, d)
    if res.get("status") == "error" and res.get("offline") and key in _last:
        return {**_last[key], "stale": True, "note": res["error"]}
    if res.get("status") != "error":
        _last[key] = res
    return res


def _check(repo: str, branch: str, d: Path | None) -> dict:
    try:
        if d is None or not d.exists():
            return {"status": "not_installed", "latest": latest_commit(repo, branch)}
        g = git_state(d)
        if not g:
            branch = (recorded(d) or {}).get("branch") or branch   # installed from another branch
        out = _check_git(repo, branch, g, d) if g else _check_files(repo, branch, d)
        if v := _version_file(d):
            out["installed"]["version"] = v
        return {**out, "checked": time.time()}
    except NotOnGitHub as e:
        return {"status": "error", "error": f"{repo}: {e}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "offline": True, "error": "GitHub didn't answer in time"}
    except Exception as e:                                   # noqa: BLE001
        msg = str(e)
        offline = any(w in msg.lower() for w in ("could not resolve", "unable to access", "timed out", "connection"))
        return {"status": "error", "offline": offline, "error": f"cannot check: {msg}"}
