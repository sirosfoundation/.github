#!/usr/bin/env python3
"""Integration branches: a documented, reproducible "bag of PRs".

An `integration/<name>` branch is GENERATED from a plan that lives in the branch itself, `.integration/plan.yaml`:

    version: 1
    base: {repo: SUNET/vc, ref: main}
    ephemeral: false                 # true: an ad-hoc test (no PR comments)
    steps:
      - merge: SUNET/vc#839          # a PR head, merged --no-ff; `@sha` pins the head
      - rebase: {pr: SUNET/vc#831}   # the PR's own commits cherry-picked onto the current tip (automatic only)
      - glue: {branch: sirosfoundation/vc:glue/839-831, note: resolves seed_recover}   # a branch resolving a conflict
    locked:                          # written by `sync`: exactly what was built; `verify` replays these
      base_sha: <40 hex>
      steps: [{head: <40 hex>}, ...]

Commands (all take --path, a clone of the repository holding the branch):

    sync    --branch B --plan FILE|--from TIP   (re)create the branch from a plan (fetches PR heads), lock it, commit the plan
    verify  --tip B                             regenerate from the locked plan and require the same tree; classify glue
    report  --tip B [--json|--summary]          what is in it: base, the steps, PR metadata, glue classification
    comment --tip B --image REF [--run-url U]   one marker comment per PR, edited in place   (--retire when the branch goes)

`verify` is the guarantee: the tip's tree (ignoring `.integration/`) must equal the replay of the plan, so no hand edit,
patch commit or change hidden in a merge commit can be on the branch. A glue branch is the only place a conflict may be
resolved, and it is classified: RESOLUTION-ONLY (every added line already exists in one of the PRs or the base: no review
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
MARKER = "<!-- siros-integration:{repo}:{branch} -->"


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

def parse_pr(spec):
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)(?:@([0-9a-f]{7,40}))?", str(spec))
    if not m:
        raise SystemExit(f"not a PR reference (owner/repo#N[@sha]): {spec}")
    return m.group(1), int(m.group(2)), m.group(3)


def parse_branch(spec):
    m = re.fullmatch(r"([\w.-]+/[\w.-]+):(.+?)(?:@([0-9a-f]{7,40}))?", str(spec))
    if not m:
        raise SystemExit(f"not a branch reference (owner/repo:branch[@sha]): {spec}")
    return m.group(1), m.group(2), m.group(3)


def load_plan(text):
    import yaml
    plan = yaml.safe_load(text)
    if not isinstance(plan, dict) or plan.get("version") != 1 or not plan.get("steps") or "base" not in plan:
        raise SystemExit("not a version 1 integration plan (needs base and steps)")
    for step in plan["steps"]:
        kinds = [k for k in ("merge", "rebase", "glue") if k in step]
        if len(kinds) != 1:
            raise SystemExit(f"a step is exactly one of merge, rebase, glue: {step}")
    return plan


def dump_plan(plan):
    import yaml
    return yaml.safe_dump(plan, sort_keys=False, width=140)


def step_kind(step):
    return next(k for k in ("merge", "rebase", "glue") if k in step)


def step_ref(step):
    """(kind, repo, ref-or-number, pinned sha) of a step."""
    kind = step_kind(step)
    if kind == "merge":
        repo, number, sha = parse_pr(step["merge"])
        return kind, repo, number, sha
    if kind == "rebase":
        repo, number, sha = parse_pr(step["rebase"]["pr"] if isinstance(step["rebase"], dict) else step["rebase"])
        return kind, repo, number, sha
    repo, branch, sha = parse_branch(step["glue"]["branch"] if isinstance(step["glue"], dict) else step["glue"])
    return kind, repo, branch, sha


# --- replay -------------------------------------------------------------------------------------------------------------

def fetch_head(path, kind, repo, ref, url_for):
    """Fetch the head of a PR (pull/<n>/head) or a branch into a private ref and return its sha."""
    name = f"refs/integration/{repo.replace('/', '_')}_{'pr' + str(ref) if kind != 'glue' else re.sub(r'[^\w.-]', '_', ref)}"
    src = f"pull/{ref}/head" if kind != "glue" else f"refs/heads/{ref}"
    fetched = git(path, "fetch", "-q", url_for(repo), f"+{src}:{name}", check=False)
    if fetched.returncode != 0:
        raise SystemExit(f"cannot fetch {repo} {src}: {fetched.stderr.strip()}")
    return git(path, "rev-parse", name).stdout.strip()


def has_object(path, sha):
    return git(path, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def pr_commits(path, head, base_sha):
    """The PR's own commits: everything reachable from its head and not from the base, oldest first, merges left out."""
    return git(path, "rev-list", "--reverse", "--no-merges", head, f"^{base_sha}").stdout.split()


def replay(path, plan, resolve, branch, base_sha, message=None):
    """Build `branch` (checked out in `path`) at base_sha by running the plan. `resolve(i, step)` returns the head sha for step i.
    Returns [{'step', 'kind', 'head', 'tip_before', 'novel_lines' (glue)}]."""
    git(path, "checkout", "-q", "-B", branch, base_sha)
    results = []
    heads = [resolve(i, step) for i, step in enumerate(plan["steps"])]
    pr_heads = [h for h, step in zip(heads, plan["steps"]) if step_kind(step) != "glue"]
    for i, step in enumerate(plan["steps"]):
        kind, repo, ref, _ = step_ref(step)
        head = heads[i]
        before = git(path, "rev-parse", "HEAD").stdout.strip()
        label = f"{repo}#{ref}" if kind != "glue" else f"{repo}:{ref}"
        if kind == "rebase":
            for sha in pr_commits(path, head, base_sha):
                pick = git(path, "cherry-pick", "--allow-empty", "--keep-redundant-commits", sha, check=False)
                if pick.returncode != 0:
                    git(path, "cherry-pick", "--abort", check=False)
                    raise SystemExit(f"rebase of {label}: {sha[:9]} does not apply automatically; a conflict needs a glue step, "
                                     f"not a hand edit")
        else:
            text = f"Merge PR {label} @{head[:9]}" if kind == "merge" else f"Glue {label} @{head[:9]}"
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
            entry["novel_lines"] = novel_lines(path, before, pr_heads, base_sha)
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
    heads = []

    def resolve(i, step):
        kind, repo, ref, pinned = step_ref(step)
        head = fetch_head(path, kind, repo, ref, url_for)
        if pinned and not head.startswith(pinned):
            raise SystemExit(f"{repo} {ref} is {head[:9]}, not the pinned {pinned}: it has moved")
        heads.append(head)
        return head

    replay(path, plan, resolve, branch, base_sha)
    locked = {**plan, "locked": {"base_sha": base_sha, "steps": [{"head": h} for h in heads]}}
    (path / ".integration").mkdir(exist_ok=True)
    (path / PLAN).write_text(dump_plan(locked))
    git(path, "add", PLAN)
    git(path, "commit", "-q", "-m", "Integration plan (generated by integration.py sync; do not edit)")
    if push_to:
        git(path, "push", "--force-with-lease", push_to, f"{branch}:{branch}")
    return locked


def fetch_head_ref(path, repo, ref, url_for):
    name = f"refs/integration/base_{repo.replace('/', '_')}_{re.sub(r'[^\w.-]', '_', ref)}"
    fetched = git(path, "fetch", "-q", url_for(repo), f"+refs/heads/{ref}:{name}", check=False)
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
            if not has_object(work, head):                 # a head that is not in the branch's own history (a rebase step)
                kind, repo, ref, _ = step_ref(step)
                fetched = fetch_head(work, kind, repo, ref, url_for)
                if fetched != head:
                    raise SystemExit(f"step {i}: the locked head {head[:9]} is no longer fetchable ({repo} {ref} is now {fetched[:9]})")
            return head
        results = replay(work, plan, resolve, "integration/verify-scratch", locked["base_sha"])
        replayed = tree_without_plan(work, "HEAD")
        actual = tree_without_plan(path, tip_sha)
        if replayed != actual:
            diff = git(path, "diff", "--stat", replayed, actual).stdout.strip().splitlines()[-1:] or ["trees differ"]
            problems.append("the branch is not what its plan produces (a hand edit, a patch or a change hidden in a merge): "
                            f"{diff[0].strip()}")
        for r in results:
            if r["kind"] == "glue":
                step = plan["steps"][r["step"]]
                glue.append({"step": r["step"], "branch": step_ref(step)[2], "head": r["head"],
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


# --- report / comment ---------------------------------------------------------------------------------------------------

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
        kind, repo, ref, _ = step_ref(step)
        entry = {"kind": kind, "repo": repo, "ref": ref, "locked_head": locked["steps"][i]["head"]}
        if kind != "glue" and with_api:
            info = pr_info(repo, ref, run)
            entry.update(info, head_moved=bool(info["head"]) and info["head"] != entry["locked_head"])
        if kind == "glue":
            entry["note"] = step["glue"].get("note") if isinstance(step["glue"], dict) else None
        steps.append(entry)
    return {"base": plan["base"], "base_sha": locked["base_sha"], "ephemeral": bool(plan.get("ephemeral")), "steps": steps}


def comment_body(rep, branch_repo, branch, image, run_url, pr):
    others = [f"{s['repo']}#{s['ref']}" for s in rep["steps"] if s["kind"] != "glue" and (s["repo"], s["ref"]) != pr]
    glue = [f"`{s['repo']}:{s['ref']}`" for s in rep["steps"] if s["kind"] == "glue"]
    lines = [MARKER.format(repo=branch_repo, branch=branch),
             f"**Included in the integration branch `{branch_repo}:{branch}`** (a test build combining several unmerged PRs; "
             "nothing here is a release).",
             "",
             f"- image: `{image}`" if image else "- image: (not built yet)",
             f"- base: {rep['base']['repo']}@{rep['base']['ref']} `{rep['base_sha'][:9]}`"]
    if others:
        lines.append("- together with: " + ", ".join(others))
    if glue:
        lines.append("- glue (conflict resolutions): " + ", ".join(glue))
    if run_url:
        lines.append(f"- built by {run_url}")
    lines += ["", "_Updated automatically; edited in place on every rebuild._"]
    return "\n".join(lines)


def upsert_comment(repo, number, body, marker, run=subprocess.run):
    comments = gh_json([f"repos/{repo}/issues/{number}/comments", "--paginate"], run) or []
    mine = next((c for c in comments if marker in (c.get("body") or "")), None)
    if mine:
        run(["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{mine['id']}", "-f", f"body={body}"], capture_output=True, text=True)
        return "updated"
    run(["gh", "api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"], capture_output=True, text=True)
    return "created"


def comment(path, tip, branch_repo, branch, image, run_url, retire=False, run=subprocess.run, dry_run=False):
    rep = report(path, tip, run)
    if rep["ephemeral"]:
        return [("ephemeral", None)]
    done = []
    for s in rep["steps"]:
        if s["kind"] == "glue":
            continue
        body = comment_body(rep, branch_repo, branch, image, run_url, (s["repo"], s["ref"]))
        if retire:
            body = body.replace("**Included in the integration branch", "**RETIRED: was included in the integration branch")
        marker = MARKER.format(repo=branch_repo, branch=branch)
        done.append((f"{s['repo']}#{s['ref']}", "dry-run" if dry_run else upsert_comment(s["repo"], s["ref"], body, marker, run)))
    return done


def summary(rep):
    return ",".join(f"{s['repo']}#{s['ref']}@{s['locked_head'][:9]}" for s in rep["steps"] if s["kind"] != "glue")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("sync", "verify", "report", "comment"))
    ap.add_argument("--path", required=True)
    ap.add_argument("--tip")
    ap.add_argument("--branch")
    ap.add_argument("--plan", help="sync: a plan file (without `locked`)")
    ap.add_argument("--from", dest="source", help="sync: re-lock the plan of this existing branch at the PRs' latest heads")
    ap.add_argument("--push", metavar="REMOTE")
    ap.add_argument("--https", action="store_true", help="fetch over https instead of ssh")
    ap.add_argument("--base", default="origin/main", help="verify --legacy: what a plan-less branch was built on")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--image", default="")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--branch-repo", default="")
    ap.add_argument("--retire", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.path)
    url_for = https_url if args.https else ssh_url
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
    if args.command == "report":
        rep = report(path, args.tip)
        print(summary(rep) if args.summary else json.dumps(rep, indent=2) if args.json else __import__("yaml").safe_dump(rep, sort_keys=False, width=140))
        return 0
    for name, what in comment(path, args.tip, args.branch_repo or "?", args.branch or args.tip, args.image, args.run_url, args.retire,
                              dry_run=args.dry_run):
        print(f"{name}: {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
