"""Scan the configured root folders for portless projects and their git worktrees.

A project is a folder with a `portless.json`, or whose `package.json` mentions portless in its
dependencies or scripts. Hostnames follow portless 0.15's rules (`inferProjectName` and
`detectWorktreePrefix` in `cli.js`), ported as-is: if the name computed here drifts from the one
portless actually registers, a running service is drawn as stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CONFIG = Path(os.environ.get("PORTLESS_MANAGER_CONFIG")
              or Path.home() / ".config/portless-manager/config.json")
# Scanned when there is no config file; only the ones that exist are used
DEFAULT_ROOTS = ("~/Developer", "~/Projects", "~/Code", "~/src", "~/dev")
DEFAULT_BRANCHES = {"main", "master"}
MAX_LABEL = 63


@dataclass
class Target:
    """One directory portless can run: the main checkout or a worktree."""
    path: Path
    name: str                   # portless name including any worktree prefix (e.g. feat.blog)
    branch: str | None = None
    worktree: bool = False
    script: str = "dev"
    runnable: bool = True       # whether package.json has that script
    note: tuple[str, dict] | None = None   # e.g. why it cannot run, as (i18n key, args)

    @property
    def key(self) -> str:
        return hashlib.sha1(str(self.path).encode()).hexdigest()[:12]


@dataclass
class Project:
    workspace: str
    path: Path
    main: Target
    worktrees: list[Target] = field(default_factory=list)

    @property
    def dirname(self) -> str:
        return self.path.name


# ── portless naming rules (ported from cli.js) ───────────────────────────
def _truncate(label: str) -> str:
    if len(label) <= MAX_LABEL:
        return label
    h = hashlib.sha256(label.encode()).hexdigest()[:6]
    return f"{label[:MAX_LABEL - 7].rstrip('-')}-{h}"


def sanitize(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]", "-", name.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return _truncate(s)


def _read_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _package_name(start: Path) -> str | None:
    """Walk up like portless does to find a package.json name, with any scope stripped."""
    d = start
    while True:
        pkg = _read_json(d / "package.json")
        if pkg and isinstance(pkg.get("name"), str) and pkg["name"]:
            return re.sub(r"^@[^/]+/", "", pkg["name"])
        if d.parent == d:
            return None
        d = d.parent


def _git_root(start: Path) -> Path | None:
    d = start
    while True:
        if (d / ".git").exists():
            return d
        if d.parent == d:
            return None
        d = d.parent


def base_name(path: Path, cfg: dict | None) -> str:
    if cfg and isinstance(cfg.get("name"), str) and cfg["name"]:
        return ".".join(_truncate(l) for l in cfg["name"].split("."))
    for cand in (_package_name(path), (_git_root(path) or path).name, path.name):
        if cand and sanitize(cand):
            return sanitize(cand)
    return sanitize(path.name)


def branch_prefix(branch: str | None) -> str | None:
    if not branch or branch == "HEAD" or branch in DEFAULT_BRANCHES:
        return None
    return sanitize(branch.split("/")[-1]) or None


def _head_branch(gitdir: Path) -> str | None:
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    m = re.match(r"^ref: refs/heads/(.+)$", head)
    return m.group(1) if m else None


# ── Discovery ─────────────────────────────────────────────────────────────
def uses_portless(path: Path) -> bool:
    if (path / "portless.json").is_file():
        return True
    pkg = _read_json(path / "package.json")
    if not pkg:
        return False
    for k in ("dependencies", "devDependencies", "optionalDependencies"):
        if isinstance(pkg.get(k), dict) and "portless" in pkg[k]:
            return True
    scripts = pkg.get("scripts")
    return isinstance(scripts, dict) and any("portless" in str(v) for v in scripts.values())


def make_target(path: Path, *, worktree: bool = False, branch: str | None = None) -> Target:
    cfg = _read_json(path / "portless.json")
    pkg = _read_json(path / "package.json") or {}
    script = (cfg or {}).get("script") or "dev"
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    base = base_name(path, cfg)
    prefix = branch_prefix(branch) if worktree else None
    t = Target(path=path, name=f"{prefix}.{base}" if prefix else base,
               branch=branch, worktree=worktree, script=script)
    if cfg and cfg.get("apps"):
        t.runnable, t.note = False, ("note.monorepo", {})
    elif script not in scripts:
        t.runnable, t.note = False, ("note.no_script", {"script": script})
    elif worktree and not prefix:
        t.note = ("note.no_prefix", {"branch": branch or "HEAD"})
    return t


def worktrees_of(repo: Path) -> list[tuple[Path, str | None]]:
    """Read `.git/worktrees/*` directly, to avoid spawning git every 10 seconds."""
    wt_dir = repo / ".git" / "worktrees"
    out = []
    try:
        entries = sorted(wt_dir.iterdir())
    except OSError:
        return out
    for e in entries:
        try:
            gitfile = Path(e.joinpath("gitdir").read_text(encoding="utf-8").strip())
        except OSError:
            continue
        wt = gitfile.parent
        if wt.is_dir():
            out.append((wt, _head_branch(e)))
    return out


def config_roots(config: Path | None = None) -> list[Path]:
    """`roots` from `config.json`. Each **direct child** of a root is a project candidate.

    ```json
    { "roots": ["~/Documents/work", "~/Documents/personal"] }
    ```
    The menu groups projects by root folder name. Without a config, the DEFAULT_ROOTS that exist.
    """
    cfg = _read_json(config or CONFIG)
    raw = cfg.get("roots") if cfg else None
    if isinstance(raw, list) and raw:
        return [Path(os.path.expanduser(str(r))) for r in raw if r]
    return [p for p in (Path(os.path.expanduser(r)) for r in DEFAULT_ROOTS) if p.is_dir()]


def discover(roots: list[Path] | None = None) -> list[Project]:
    projects = []
    for root in config_roots() if roots is None else roots:
        try:
            dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        for d in dirs:
            if not uses_portless(d):
                continue
            proj = Project(workspace=root.name, path=d, main=make_target(d))
            proj.worktrees = [make_target(p, worktree=True, branch=b) for p, b in worktrees_of(d)]
            projects.append(proj)
    # A worktree created inside a root is also found as a folder; keep it only under its repo
    wt_paths = {w.path.resolve() for p in projects for w in p.worktrees}
    return [p for p in projects if p.path.resolve() not in wt_paths]
