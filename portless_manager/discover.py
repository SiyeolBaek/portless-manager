"""설정한 루트 디렉터리들을 훑어 portless 대상 프로젝트와 그 worktree 를 찾는다.

대상 = `portless.json` 이 있거나 `package.json` 의 의존성·스크립트에 portless 가 들어 있는 폴더.
호스트명은 portless 0.15 의 규칙(`cli.js` 의 inferProjectName · detectWorktreePrefix)을 그대로
옮겼다 — 여기서 계산한 이름과 portless 가 실제로 등록하는 이름이 어긋나면 실행 중인 서비스를
「정지」로 잘못 그린다.
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
# 설정 파일이 없을 때 훑는 곳 — 존재하는 것만 쓴다
DEFAULT_ROOTS = ("~/Developer", "~/Projects", "~/Code", "~/src", "~/dev")
DEFAULT_BRANCHES = {"main", "master"}
MAX_LABEL = 63


@dataclass
class Target:
    """portless 로 띄울 수 있는 디렉터리 하나 — 본 체크아웃이거나 worktree."""
    path: Path
    name: str                   # 워크트리 접두사까지 붙은 portless 이름 (예: feat.bada)
    branch: str | None = None
    worktree: bool = False
    script: str = "dev"
    runnable: bool = True       # package.json 에 그 스크립트가 있는가
    note: str = ""              # 실행 불가 사유 등

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


# ── portless 이름 규칙 (cli.js 이식) ─────────────────────────────────────
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
    """portless 와 같이 부모 방향으로 package.json 의 name 을 찾는다 (스코프 제거)."""
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


# ── 탐색 ──────────────────────────────────────────────────────────────────
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
        t.runnable, t.note = False, "모노레포(apps) 는 아직 지원하지 않음"
    elif script not in scripts:
        t.runnable, t.note = False, f'"{script}" 스크립트 없음'
    elif worktree and not prefix:
        t.note = f"{branch or 'detached'} 브랜치라 본 체크아웃과 이름이 같다"
    return t


def worktrees_of(repo: Path) -> list[tuple[Path, str | None]]:
    """`.git/worktrees/*` 를 직접 읽는다 — 10초마다 git 을 띄우지 않으려고."""
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
    """`config.json` 의 `roots` — 각 루트의 **바로 아래 폴더**가 프로젝트 후보다.

    ```json
    { "roots": ["~/Documents/work", "~/Documents/personal"] }
    ```
    메뉴에는 루트 폴더 이름으로 묶여 나온다. 설정이 없으면 DEFAULT_ROOTS 중 있는 것.
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
    # 루트 안에 만든 worktree 는 폴더로도 잡힌다 — 본 저장소 밑에만 둔다
    wt_paths = {w.path.resolve() for p in projects for w in p.worktrees}
    return [p for p in projects if p.path.resolve() not in wt_paths]
