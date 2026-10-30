#!/usr/bin/env python3
"""Integration branches: a documented, reproducible "bag of changes", and what is built from it.

An `integration/<name>` branch is GENERATED from a plan that lives in the branch itself, `.integration/plan.yaml`:

    version: 2
    base: {repo: SUNET/vc, ref: main}   # a branch, or a full 40-hex commit (a reproducible base)
    ephemeral: false                    # true: an ad-hoc test (reserved: used by the comments follow-up)
    steps:                              # a SOURCE is a PR `owner/repo#N[@sha]` or a branch `owner/repo:branch[@sha]`
      - merge: SUNET/vc#839             # the source's head, merged --no-ff; `@sha` pins the head
      - merge: sirosfoundation/vc:docs/operator        # a branch of a fork is a source as well
      - rebase: SUNET/vc#831            # the source's own commits cherry-picked onto the current tip (automatic only)
      - pick: {from: sirosfoundation/vc:fix/logs, commits: [5da879895]}   # exactly these commits (reachable from the source)
      - glue: {branch: sirosfoundation/vc:glue/839-831, note: resolves seed_recover}   # a branch resolving a conflict
    builds:                             # what the workflow builds from the branch (see below); optional
      - {name: apigw, kind: docker, image: sirosfoundation/integration/vc-apigw, build_args: [SERVICE_NAME=apigw]}
      - {name: sdk, kind: artifact, runner: ubuntu-latest, run: ./gradlew assembleRelease, outputs: ['**/build/outputs/aar/*.aar']}
    locked:                             # written by `sync`: exactly what was built; `verify` replays these
      base_sha: <40 hex>
      steps: [{head: <40 hex>}, {head: <40 hex>, commits: [<40 hex>]}, ...]

Version 1 plans (PR-only merge/rebase, glue) remain valid.

Commands (all take --path, a clone of the repository holding the branch):

    sync    --branch B --plan FILE|--from TIP   (re)create the branch from a plan (fetches the sources), lock it, commit the plan
    verify  --tip B                             regenerate from the locked plan and require the same tree; classify glue
    report  --tip B [--json|--summary]          what is in it: base, the steps, PR metadata, glue classification
    builds  --tip B [--json]                    the plan's build targets, validated (the workflow turns them into a matrix)

`--remotes DIR` makes every `owner/repo` a local repository at DIR/owner/repo (offline and tests; the default is GitHub over ssh,
or https with --https).

`verify` is the guarantee: the tip's tree (ignoring `.integration/`) must equal the replay of the plan, so no hand edit,
patch commit or change hidden in a merge commit can be on the branch. A glue branch is the only place a conflict may be
resolved, and it is classified: RESOLUTION-ONLY (every added line already exists in one of the sources or the base: no review
needed) or NEW CODE (lines found nowhere else: it needs a draft PR and review like any change).

A branch without a plan (older ones) is still reported from its history (`report`), and `verify --legacy` checks it the old
way: only merges, none hand-edited.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PLAN = ".integration/plan.yaml"


def git(path, *args, check=True, input=None):
    out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, input=input)
    if check and out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out


def ssh_url(repo):
    return f"git@github.com:{repo}.git"


def https_url(repo):
    return f"https://github.com/{repo}.git"


# --- plan ---------------------------------------------------------------------------------------------------------------

PR_RE = r"([\w.-]+/[\w.-]+)#(\d+)(?:@([0-9a-f]{7,40}))?"
BRANCH_RE = r"([\w.-]+/[\w.-]+):(.+?)(?:@([0-9a-f]{7,40}))?"
STEP_KINDS = ("merge", "rebase", "pick", "glue")
BUILD_KINDS = ("docker", "artifact")


def parse_pr(spec):
    m = re.fullmatch(PR_RE, str(spec))
    if not m:
        raise SystemExit(f"not a PR reference (owner/repo#N[@sha]): {spec}")
    return m.group(1), int(m.group(2)), m.group(3)


def parse_branch(spec):
    m = re.fullmatch(BRANCH_RE, str(spec))
    if not m:
        raise SystemExit(f"not a branch reference (owner/repo:branch[@sha]): {spec}")
    return m.group(1), m.group(2), m.group(3)


def parse_source(spec):
    """A PR (owner/repo#N[@sha]) or a branch (owner/repo:branch[@sha]) -> {'kind': 'pr'|'branch', 'repo', 'ref', 'pinned'}."""
    spec = str(spec)
    if re.fullmatch(PR_RE, spec):
        repo, number, sha = parse_pr(spec)
        return {"kind": "pr", "repo": repo, "ref": number, "pinned": sha}
    if re.fullmatch(BRANCH_RE, spec):
        repo, branch, sha = parse_branch(spec)
        return {"kind": "branch", "repo": repo, "ref": branch, "pinned": sha}
    raise SystemExit(f"not a source (a PR owner/repo#N[@sha] or a branch owner/repo:branch[@sha]): {spec}")


def load_plan(text):
    import yaml
    plan = yaml.safe_load(text)
    if not isinstance(plan, dict) or plan.get("version") not in (1, 2) or not plan.get("steps") or "base" not in plan:
        raise SystemExit("not a version 1 or 2 integration plan (needs base and steps)")
    for step in plan["steps"]:
        kinds = [k for k in STEP_KINDS if k in step]
        if len(kinds) != 1:
            raise SystemExit(f"a step is exactly one of {', '.join(STEP_KINDS)}: {step}")
        src = step_source(step)                 # refuses a malformed source or commit list
        if plan["version"] == 1 and (step_kind(step) == "pick" or (src["kind"] == "branch" and step_kind(step) != "glue")):
            raise SystemExit("a version 1 plan has PR merges and rebases and glue branches only: use version: 2 for pick steps and branch sources")
    if plan["version"] == 1 and plan.get("builds"):
        raise SystemExit("builds need version: 2")
    parse_builds(plan)
    return plan


def dump_plan(plan):
    import yaml
    return yaml.safe_dump(plan, sort_keys=False, width=140)


def step_kind(step):
    return next(k for k in STEP_KINDS if k in step)


def step_source(step):
    """The source of a step: {'kind': 'pr'|'branch', 'repo', 'ref', 'pinned'} (+ 'commits' for a pick)."""
    kind = step_kind(step)
    body = step[kind]
    if kind == "glue":
        src = parse_source(body["branch"] if isinstance(body, dict) else body)
        if src["kind"] != "branch":
            raise SystemExit(f"a glue step names a branch (owner/repo:branch): {body}")
        return src
    if kind == "pick":
        if not isinstance(body, dict) or "from" not in body or not body.get("commits"):
            raise SystemExit(f"a pick step is {{from: <source>, commits: [<sha>, ...]}}: {step}")
        src = parse_source(body["from"])
        commits = [str(c) for c in body["commits"]]
        for c in commits:
            if not re.fullmatch(r"[0-9a-f]{7,40}", c):
                raise SystemExit(f"not a commit sha: {c}")
        return {**src, "commits": commits}
    if isinstance(body, dict):                  # rebase: {pr: ...} (version 1) or {from: ...}
        body = body.get("from", body.get("pr"))
    return parse_source(body)


def step_ref(step):
    """(kind, repo, ref, pinned sha) of a step: ref is the PR number or the branch name."""
    src = step_source(step)
    return step_kind(step), src["repo"], src["ref"], src["pinned"]


def step_label(step):
    src = step_source(step)
    return f"{src['repo']}#{src['ref']}" if src["kind"] == "pr" else f"{src['repo']}:{src['ref']}"


# --- builds: what the workflow makes of the branch -----------------------------------------------------------------------

BUILD_NAME = r"[a-z0-9][a-z0-9._-]*"
DOCKER_KEYS = {"name", "kind", "image", "dockerfile", "context", "target", "build_args", "platforms"}
ARTIFACT_KEYS = {"name", "kind", "runner", "run", "outputs", "env"}
# GitHub-hosted runners only: a plan is data pushed to a branch and must not be able to pick a self-hosted machine.
RUNNERS = ("ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04", "macos-latest", "macos-15", "macos-14", "windows-latest")


def parse_builds(plan):
    """Validate and normalise plan['builds'] -> [{'name', 'kind', ...defaults filled}]. A build is data, not code: `docker` names a
    Dockerfile and its arguments; `artifact` is a shell command whose outputs (globs) are collected (an SDK's AAR or APK, an
    XCFramework, a package), which is how native-app and SDK builds are expressed. Unknown keys are refused."""
    out, seen = [], set()
    for build in plan.get("builds") or []:
        if not isinstance(build, dict) or build.get("kind") not in BUILD_KINDS:
            raise SystemExit(f"a build is a mapping with kind one of {', '.join(BUILD_KINDS)}: {build}")
        name = str(build.get("name", ""))
        if not re.fullmatch(BUILD_NAME, name) or name in seen:
            raise SystemExit(f"a build needs a unique name matching {BUILD_NAME}: {name!r}")
        seen.add(name)
        allowed = DOCKER_KEYS if build["kind"] == "docker" else ARTIFACT_KEYS
        extra = set(build) - allowed
        if extra:
            raise SystemExit(f"build {name}: unknown keys {sorted(extra)} for kind {build['kind']}")
        if build["kind"] == "docker":
            image = str(build.get("image", ""))
            if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", image):
                raise SystemExit(f"build {name}: image is a GHCR path without registry or tag (e.g. sirosfoundation/integration/vc-apigw)")
            args = build.get("build_args") or []
            if isinstance(args, dict):
                args = [f"{k}={v}" for k, v in args.items()]
            if not all(isinstance(a, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", a) for a in args):
                raise SystemExit(f"build {name}: build_args are KEY=VALUE strings or a mapping")
            out.append({"name": name, "kind": "docker", "image": image, "dockerfile": build.get("dockerfile", "./Dockerfile"),
                        "context": build.get("context", "."), "target": build.get("target", ""), "build_args": list(args),
                        "platforms": build.get("platforms", "linux/amd64")})
        else:
            outputs = build.get("outputs")
            if not outputs or not all(isinstance(o, str) and o and not o.startswith("/") and ".." not in o.split("/") for o in outputs):
                raise SystemExit(f"build {name}: outputs are relative globs (at least one): {outputs}")
            runner = build.get("runner", "ubuntu-latest")
            if runner not in RUNNERS:
                raise SystemExit(f"build {name}: runner is a GitHub-hosted one of {', '.join(RUNNERS)}")
            run = build.get("run")
            if not isinstance(run, str) or not run.strip():
                raise SystemExit(f"build {name}: run is the shell commands that produce the outputs")
            env = build.get("env") or {}
            if not isinstance(env, dict) or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) for k in env):
                raise SystemExit(f"build {name}: env is a mapping of variable names to values")
            out.append({"name": name, "kind": "artifact", "runner": runner, "run": run,
                        "outputs": list(outputs), "env": {str(k): str(v) for k, v in env.items()}})
    return out


# --- replay -------------------------------------------------------------------------------------------------------------

def fetch_head(path, src, url_for):
    """Fetch the head of a source (a PR's pull/<n>/head, or a branch) into a private ref and return its sha."""
    repo, ref = src["repo"], src["ref"]
    tail = f"pr{ref}" if src["kind"] == "pr" else re.sub(r"[^\w.-]", "_", ref)
    name = f"refs/integration/{repo.replace('/', '_')}_{tail}"
    remote = f"pull/{ref}/head" if src["kind"] == "pr" else f"refs/heads/{ref}"
    fetched = git(path, "fetch", "-q", url_for(repo), f"+{remote}:{name}", check=False)
    if fetched.returncode != 0:
        raise SystemExit(f"cannot fetch {repo} {remote}: {fetched.stderr.strip()}")
    return git(path, "rev-parse", name).stdout.strip()


def resolve_commits(path, head, commits):
    """Full shas of a pick's commits; each must be reachable from the source head (a pick takes commits OF that source)."""
    full = []
    for c in commits:
        out = git(path, "rev-parse", "--verify", "-q", f"{c}^{{commit}}", check=False)
        if out.returncode != 0:
            raise SystemExit(f"pick: commit {c} is not in the fetched source")
        sha = out.stdout.strip()
        if git(path, "merge-base", "--is-ancestor", sha, head, check=False).returncode != 0:
            raise SystemExit(f"pick: commit {c} is not reachable from the source head {head[:9]}")
        full.append(sha)
    return full


def has_object(path, sha):
    return git(path, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def pr_commits(path, head, base_sha):
    """The PR's own commits: everything reachable from its head and not from the base, oldest first, merges left out."""
    return git(path, "rev-list", "--reverse", "--no-merges", head, f"^{base_sha}").stdout.split()


def replay(path, plan, resolve, branch, base_sha, commits_of=None):
    """Build `branch` (checked out in `path`) at base_sha by running the plan. `resolve(i, step)` returns the head sha for step i;
    `commits_of(i, step, head)` the full shas of a pick step. Returns [{'step', 'kind', 'head', 'tip_before', 'novel_lines' (glue)}]."""
    git(path, "checkout", "-q", "-B", branch, base_sha)
    results = []
    heads = [resolve(i, step) for i, step in enumerate(plan["steps"])]
    known_heads = [h for h, step in zip(heads, plan["steps"]) if step_kind(step) != "glue"]
    for i, step in enumerate(plan["steps"]):
        kind, head = step_kind(step), heads[i]
        before = git(path, "rev-parse", "HEAD").stdout.strip()
        label = step_label(step)
        if kind in ("rebase", "pick"):
            shas = pr_commits(path, head, base_sha) if kind == "rebase" else commits_of(i, step, head)
            for sha in shas:
                pick = git(path, "cherry-pick", "--allow-empty", "--keep-redundant-commits", sha, check=False)
                if pick.returncode != 0:
                    git(path, "cherry-pick", "--abort", check=False)
                    raise SystemExit(f"{kind} of {label}: {sha[:9]} does not apply automatically; a conflict needs a glue step, "
                                     f"not a hand edit")
        else:
            text = f"Merge {label} @{head[:9]}" if kind == "merge" else f"Glue {label} @{head[:9]}"
            note = step.get("note") or (step["glue"].get("note") if kind == "glue" and isinstance(step["glue"], dict) else None)
            if note:
                text += f": {note}"
            merged = git(path, "merge", "--no-ff", "-m", text, head, check=False)
            if merged.returncode != 0:
                git(path, "merge", "--abort", check=False)
                raise SystemExit(f"{label} @{head[:9]} conflicts with what is already on {branch}. A conflict is resolved on a "
                                 f"glue branch that goes into the plan (a `glue` step before this one), never by hand: "
                                 f"{merged.stdout.strip()[:300]}")
        entry = {"step": i, "kind": kind, "head": head, "tip_before": before}
        if kind == "glue":
            entry["novel_lines"] = novel_lines(path, before, known_heads, base_sha)
        results.append(entry)
    return results


def file_lines(path, rev, name):
    out = git(path, "show", f"{rev}:{name}", check=False)
    return set(out.stdout.splitlines()) if out.returncode == 0 else set()


def novel_lines(path, before, pr_heads, base_sha):
    """Added lines of a glue step that exist in none of: the tip before it, the base, or ANY pull-request head of the plan (earlier
    or later: a glue resolves a conflict with a PR that is merged after it). {file: [line, ...]}. Empty means RESOLUTION-ONLY. The
    glue branch itself is deliberately not a source: its own lines are what is being judged."""
    after = git(path, "rev-parse", "HEAD").stdout.strip()
    changed = git(path, "diff", "--name-only", before, after).stdout.split()
    novel = {}
    for name in changed:
        added = [l[1:] for l in git(path, "diff", "-U0", before, after, "--", name).stdout.splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        known = set()
        for rev in [before, base_sha, *pr_heads]:
            known |= file_lines(path, rev, name)
        fresh = [l for l in added if l.strip() and l not in known]
        if fresh:
            novel[name] = fresh
    return novel


def tree_without_plan(path, rev):
    """The tree id of `rev` with `.integration/` removed."""
    with tempfile.NamedTemporaryFile() as idx:
        env_git = ["git", "-C", str(path)]
        import os
        env = {**os.environ, "GIT_INDEX_FILE": idx.name}
        subprocess.run([*env_git, "read-tree", rev], env=env, check=True, capture_output=True)
        subprocess.run([*env_git, "rm", "-r", "-q", "--cached", "--ignore-unmatch", ".integration"], env=env, check=True,
                       capture_output=True)
        return subprocess.run([*env_git, "write-tree"], env=env, check=True, capture_output=True, text=True).stdout.strip()


# --- sync / verify ------------------------------------------------------------------------------------------------------

def sync(path, branch, plan, url_for=ssh_url, push_to=None):
    """(Re)create `branch` from `plan`: fetch every head, replay, lock the heads, commit the plan. Returns the locked plan."""
    path = Path(path)
    if git(path, "status", "--porcelain").stdout.strip():
        raise SystemExit("the working tree is not clean")
    base_repo, base_ref = plan["base"]["repo"], plan["base"]["ref"]
    base_sha = fetch_head_ref(path, base_repo, base_ref, url_for)
    heads, picks = [], {}

    def resolve(i, step):
        src = step_source(step)
        head = fetch_head(path, src, url_for)
        if src["pinned"] and not head.startswith(src["pinned"]):
            raise SystemExit(f"{step_label(step)} is {head[:9]}, not the pinned {src['pinned']}: it has moved")
        heads.append(head)
        if step_kind(step) == "pick":
            picks[i] = resolve_commits(path, head, src["commits"])
        return head

    replay(path, plan, resolve, branch, base_sha, commits_of=lambda i, step, head: picks[i])
    steps_locked = [{"head": h, **({"commits": picks[i]} if i in picks else {})} for i, h in enumerate(heads)]
    locked = {**plan, "locked": {"base_sha": base_sha, "steps": steps_locked}}
    (path / ".integration").mkdir(exist_ok=True)
    (path / PLAN).write_text(dump_plan(locked))
    git(path, "add", PLAN)
    git(path, "commit", "-q", "-m", "Integration plan (generated by integration.py sync; do not edit)")
    if push_to:
        git(path, "push", "--force-with-lease", push_to, f"{branch}:{branch}")
    return locked


def fetch_head_ref(path, repo, ref, url_for):
    """The base: a branch name, or a full 40-hex commit (a reproducible base)."""
    name = f"refs/integration/base_{repo.replace('/', '_')}_{re.sub(r'[^\w.-]', '_', ref)}"
    src = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else f"refs/heads/{ref}"
    fetched = git(path, "fetch", "-q", url_for(repo), f"+{src}:{name}", check=False)
    if fetched.returncode != 0:
        raise SystemExit(f"cannot fetch the base {repo}@{ref}: {fetched.stderr.strip()}")
    return git(path, "rev-parse", name).stdout.strip()


def read_plan(path, tip):
    out = git(path, "show", f"{tip}:{PLAN}", check=False)
    if out.returncode != 0:
        return None
    plan = load_plan(out.stdout)
    if "locked" not in plan:
        raise SystemExit("the plan is not locked: run sync")
    return plan


def verify(path, tip, url_for=ssh_url):
    """Replay the locked plan in a scratch worktree and compare trees. Returns {'ok', 'problems', 'glue'}."""
    path = Path(path)
    plan = read_plan(path, tip)
    if plan is None:
        return {"ok": False, "problems": [f"no {PLAN} on {tip}: a branch is generated from a plan"], "glue": []}
    locked = plan["locked"]
    tip_sha = git(path, "rev-parse", f"{tip}^{{commit}}").stdout.strip()
    problems, glue = [], []
    work = Path(tempfile.mkdtemp())
    git(path, "worktree", "add", "-q", "--detach", str(work), tip_sha)
    try:
        def resolve(i, step):
            head = locked["steps"][i]["head"]
            needed = [head] + locked["steps"][i].get("commits", [])
            if not all(has_object(work, o) for o in needed):    # not in the branch's own history (a rebase or pick step)
                fetched = fetch_head(work, step_source(step), url_for)
                if fetched != head:
                    raise SystemExit(f"step {i}: the locked head {head[:9]} is no longer fetchable ({step_label(step)} is now {fetched[:9]})")
            return head
        results = replay(work, plan, resolve, "integration/verify-scratch", locked["base_sha"],
                         commits_of=lambda i, step, head: locked["steps"][i]["commits"])
        replayed = tree_without_plan(work, "HEAD")
        actual = tree_without_plan(path, tip_sha)
        if replayed != actual:
            diff = git(path, "diff", "--stat", replayed, actual).stdout.strip().splitlines()[-1:] or ["trees differ"]
            problems.append("the branch is not what its plan produces (a hand edit, a patch or a change hidden in a merge): "
                            f"{diff[0].strip()}")
        for r in results:
            if r["kind"] == "glue":
                step = plan["steps"][r["step"]]
                glue.append({"step": r["step"], "branch": step_source(step)["ref"], "head": r["head"],
                             "class": "new code" if r["novel_lines"] else "resolution-only",
                             "new_lines": sum(len(v) for v in r["novel_lines"].values()), "files": sorted(r["novel_lines"])})
    except SystemExit as e:
        problems.append(str(e))
    finally:
        git(path, "worktree", "remove", "--force", str(work), check=False)
        git(path, "branch", "-D", "integration/verify-scratch", check=False)
    return {"ok": not problems, "problems": problems, "glue": glue}


# --- legacy (no plan): derive from history ------------------------------------------------------------------------------

def legacy_problems(path, tip, base_ref):
    """For a branch without a plan: every first-parent step must be a merge whose result is the automatic merge of its parents."""
    tip_sha = git(path, "rev-parse", "--verify", f"{tip}^{{commit}}").stdout.strip()
    base = git(path, "merge-base", tip_sha, base_ref).stdout.strip()
    out = []
    for line in git(path, "log", "--first-parent", "--reverse", "--format=%H|%P|%T|%s", f"{base}..{tip_sha}").stdout.splitlines():
        sha, parents, tree, subject = line.split("|", 3)
        parents = parents.split()
        if len(parents) != 2:
            out.append(f"{sha[:9]} {subject[:60]}: not a merge of a pull request")
            continue
        replay_ = git(path, "merge-tree", "--write-tree", "--no-messages", *parents, check=False)
        if replay_.returncode != 0:
            out.append(f"{sha[:9]} {subject[:60]}: conflicts when replayed (resolved by hand inside the merge)")
        elif replay_.stdout.split()[0] != tree:
            out.append(f"{sha[:9]} {subject[:60]}: differs from the automatic merge of its parents")
    return out


# --- report -------------------------------------------------------------------------------------------------------------

def gh_json(args, run=subprocess.run):
    out = run(["gh", "api", *args], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def pr_info(repo, number, run=subprocess.run):
    data = gh_json([f"repos/{repo}/pulls/{number}"], run)
    if not data:
        return {"state": "unknown", "title": "", "draft": False, "head": None, "labels": []}
    return {"state": "merged" if data.get("merged_at") else data["state"], "title": data["title"], "draft": data.get("draft", False),
            "head": data["head"]["sha"], "labels": [l["name"] for l in data.get("labels", [])]}


def report(path, tip, run=subprocess.run, with_api=True):
    plan = read_plan(Path(path), tip)
    if plan is None:
        raise SystemExit(f"no {PLAN} on {tip}")
    locked = plan["locked"]
    steps = []
    for i, step in enumerate(plan["steps"]):
        kind, src = step_kind(step), step_source(step)
        entry = {"kind": kind, "repo": src["repo"], "ref": src["ref"], "source": src["kind"], "locked_head": locked["steps"][i]["head"]}
        if kind == "pick":
            entry["commits"] = [c[:9] for c in locked["steps"][i]["commits"]]
        if src["kind"] == "pr" and kind != "glue" and with_api:
            info = pr_info(src["repo"], src["ref"], run)
            entry.update(info, head_moved=bool(info["head"]) and info["head"] != entry["locked_head"])
        if kind == "glue":
            entry["note"] = step["glue"].get("note") if isinstance(step["glue"], dict) else None
        steps.append(entry)
    return {"base": plan["base"], "base_sha": locked["base_sha"], "ephemeral": bool(plan.get("ephemeral")), "steps": steps,
            "builds": [b["name"] for b in parse_builds(plan)]}


def summary(rep):
    return ",".join(f"{s['repo']}{'#' if s['source'] == 'pr' else ':'}{s['ref']}@{s['locked_head'][:9]}" for s in rep["steps"] if s["kind"] != "glue")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("sync", "verify", "report", "builds"))
    ap.add_argument("--path", required=True)
    ap.add_argument("--tip")
    ap.add_argument("--branch")
    ap.add_argument("--plan", help="sync: a plan file (without `locked`)")
    ap.add_argument("--from", dest="source", help="sync: re-lock the plan of this existing branch at the PRs' latest heads")
    ap.add_argument("--push", metavar="REMOTE")
    ap.add_argument("--https", action="store_true", help="fetch over https instead of ssh")
    ap.add_argument("--remotes", metavar="DIR", help="every owner/repo is a local repository at DIR/owner/repo (offline, tests)")
    ap.add_argument("--base", default="origin/main", help="verify --legacy: what a plan-less branch was built on")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.path)
    url_for = (lambda repo: str(Path(args.remotes) / repo)) if args.remotes else https_url if args.https else ssh_url
    if args.command == "sync":
        if not args.branch:
            raise SystemExit("sync needs --branch")
        if args.plan:
            plan = load_plan(Path(args.plan).read_text())
        elif args.source:
            plan = read_plan(path, args.source)
        else:
            raise SystemExit("sync needs --plan or --from")
        plan.pop("locked", None)
        sync(path, args.branch, plan, url_for, args.push)
        print(f"{args.branch}: {len(plan['steps'])} steps, locked")
        return 0
    if not args.tip:
        raise SystemExit(f"{args.command} needs --tip")
    if args.command == "verify":
        if args.legacy:
            problems = legacy_problems(path, args.tip, args.base)
            for p in problems:
                print(f"DRIFT {p}", file=sys.stderr)
            print("FAILED" if problems else "OK", file=sys.stderr)
            return 1 if problems else 0
        result = verify(path, args.tip, url_for)
        for p in result["problems"]:
            print(f"DRIFT {p}", file=sys.stderr)
        for g in result["glue"]:
            print(f"glue {g['branch']}: {g['class']}" + (f" ({g['new_lines']} new lines in {', '.join(g['files'])})" if g["new_lines"] else ""),
                  file=sys.stderr)
        print("OK" if result["ok"] else "FAILED", file=sys.stderr)
        return 0 if result["ok"] else 1
    if args.command == "builds":
        plan = read_plan(path, args.tip)
        if plan is None:
            raise SystemExit(f"no {PLAN} on {args.tip}")
        builds = parse_builds(plan)
        print(json.dumps({"docker": [b for b in builds if b["kind"] == "docker"], "artifact": [b for b in builds if b["kind"] == "artifact"]}))
        return 0
    if args.command == "report":
        rep = report(path, args.tip)
        print(summary(rep) if args.summary else json.dumps(rep, indent=2) if args.json else __import__("yaml").safe_dump(rep, sort_keys=False, width=140))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
