#!/usr/bin/env python3
"""actions/integration/integration.py against real git repositories (the "remotes" are local repos with refs/pull/N/head)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import integration  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})


class Remote:
    """A repository holding `main`, PR heads under refs/pull/N/head and glue branches."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.git("init", "-q", "-b", "main")
        self.write("a.txt", "base\n")
        self.commit("base")

    def git(self, *args):
        out = subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, text=True, env=ENV)
        assert out.returncode == 0, f"git {args}: {out.stderr}"
        return out.stdout.strip()

    def write(self, name, text):
        (self.dir / name).write_text(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def pr(self, number, filename, text, base="main"):
        self.git("checkout", "-q", "-B", f"pr{number}", base)
        self.write(filename, text)
        head = self.commit(f"feat: pr {number}")
        self.git("update-ref", f"refs/pull/{number}/head", head)
        self.git("checkout", "-q", "main")
        return head

    def branch(self, name, files, base="main"):
        """A branch with one commit per entry of `files` [(file, text), ...] (a fork's feature branch); returns the commits."""
        self.git("checkout", "-q", "-B", name, base)
        commits = []
        for filename, text in files:
            self.write(filename, text)
            commits.append(self.commit(f"{name}: {filename}"))
        self.git("checkout", "-q", "main")
        return commits

    def glue(self, name, parents, resolved):
        """A branch that merges the given PR branches (conflicting) and commits `resolved` {file: text}."""
        self.git("checkout", "-q", "-B", name, f"pr{parents[0]}")
        out = subprocess.run(["git", "-C", str(self.dir), "merge", "--no-commit", "--no-ff", f"pr{parents[1]}"], capture_output=True, text=True, env=ENV)
        assert out.returncode != 0, "expected a conflict"
        for f, t in resolved.items():
            self.write(f, t)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"resolve {name}")
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return head


class Case(unittest.TestCase):
    def setUp(self):
        self.remote = Remote()
        self.clone = Path(tempfile.mkdtemp())
        subprocess.run(["git", "clone", "-q", str(self.remote.dir), str(self.clone)], check=True, env=ENV)
        self.url = lambda repo: str(self.remote.dir)

    def plan(self, steps, ephemeral=False):
        return {"version": 1, "base": {"repo": "o/r", "ref": "main"}, "ephemeral": ephemeral, "steps": steps}

    def sync(self, steps, **kw):
        return integration.sync(self.clone, "integration/x", self.plan(steps, **kw), self.url)

    def commit_on_branch(self, name, text):
        (self.clone / name).write_text(text)
        subprocess.run(["git", "-C", str(self.clone), "add", "-A"], check=True, env=ENV)
        subprocess.run(["git", "-C", str(self.clone), "commit", "-q", "-m", "hand edit"], check=True, env=ENV)


class SyncAndVerify(Case):
    def test_a_synced_branch_verifies_and_records_the_locked_heads(self):
        h1 = self.remote.pr(1, "one.txt", "1\n")
        h2 = self.remote.pr(2, "two.txt", "2\n")
        locked = self.sync([{"merge": "o/r#1"}, {"merge": "o/r#2"}])
        self.assertEqual([s["head"] for s in locked["locked"]["steps"]], [h1, h2])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertEqual((result["ok"], result["problems"]), (True, []))

    def test_a_patch_commit_on_the_branch_makes_verify_fail(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.sync([{"merge": "o/r#1"}])
        self.commit_on_branch("patch.txt", "not from any PR\n")
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertFalse(result["ok"])
        self.assertIn("not what its plan produces", result["problems"][0])

    def test_a_change_hidden_inside_a_merge_commit_makes_verify_fail(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.remote.pr(2, "two.txt", "2\n")
        self.sync([{"merge": "o/r#1"}, {"merge": "o/r#2"}])
        # rewrite the history: amend the last merge to smuggle a file in, then re-commit the plan on top
        c = self.clone
        run = lambda *a: subprocess.run(["git", "-C", str(c), *a], check=True, env=ENV, capture_output=True, text=True)
        plan_text = run("show", f"HEAD:{integration.PLAN}").stdout
        run("reset", "-q", "--hard", "HEAD~1")
        (c / "smuggled.txt").write_text("hidden\n")
        run("add", "-A")
        run("commit", "-q", "--amend", "--no-edit")
        (c / ".integration").mkdir(exist_ok=True)
        (c / integration.PLAN).write_text(plan_text)
        run("add", "-A")
        run("commit", "-q", "-m", "plan")
        self.assertFalse(integration.verify(c, "integration/x", self.url)["ok"])

    def test_verify_replays_the_locked_heads_even_if_the_pr_has_since_moved(self):
        h1 = self.remote.pr(1, "one.txt", "1\n")
        self.sync([{"merge": "o/r#1"}])
        self.remote.git("checkout", "-q", "pr1")
        self.remote.write("one.txt", "1b\n")
        moved = self.remote.commit("feat: pr 1 moved")
        self.remote.git("update-ref", "refs/pull/1/head", moved)
        self.assertEqual(integration.verify(self.clone, "integration/x", self.url)["ok"], True)
        self.assertNotEqual(h1, moved)

    def test_a_pinned_head_that_moved_is_refused(self):
        self.remote.pr(1, "one.txt", "1\n")
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "o/r#1@" + "0" * 9}])
        self.assertIn("has moved", str(cm.exception))

    def test_a_branch_without_a_plan_fails_verify(self):
        subprocess.run(["git", "-C", str(self.clone), "checkout", "-q", "-b", "integration/old"], check=True, env=ENV)
        result = integration.verify(self.clone, "integration/old", self.url)
        self.assertFalse(result["ok"])
        self.assertIn("generated from a plan", result["problems"][0])

    def test_from_relocks_an_existing_plan_at_the_latest_heads(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.sync([{"merge": "o/r#1"}])
        self.remote.git("checkout", "-q", "pr1")
        self.remote.write("one.txt", "1b\n")
        newer = self.remote.commit("feat: pr 1 moved")
        self.remote.git("update-ref", "refs/pull/1/head", newer)
        plan = integration.read_plan(self.clone, "integration/x")
        plan.pop("locked")
        self.clone_run("checkout", "-q", "main")
        locked = integration.sync(self.clone, "integration/y", plan, self.url)
        self.assertEqual(locked["locked"]["steps"][0]["head"], newer)

    def clone_run(self, *a):
        subprocess.run(["git", "-C", str(self.clone), *a], check=True, env=ENV, capture_output=True)


class Conflicts(Case):
    def setUp(self):
        super().setUp()
        self.remote.pr(1, "a.txt", "one\n")
        self.remote.pr(2, "a.txt", "two\n")

    def test_a_conflict_stops_the_sync_and_demands_a_glue_step(self):
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "o/r#1"}, {"merge": "o/r#2"}])
        self.assertIn("glue", str(cm.exception))
        self.assertEqual(subprocess.run(["git", "-C", str(self.clone), "status", "--porcelain"], capture_output=True, text=True).stdout, "")

    def test_a_glue_that_only_chooses_between_existing_lines_is_resolution_only(self):
        self.remote.glue("glue/1-2", [1, 2], {"a.txt": "one\ntwo\n"})
        self.sync([{"merge": "o/r#1"}, {"glue": {"branch": "o/r:glue/1-2", "note": "keep both"}}, {"merge": "o/r#2"}])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual([(g["class"], g["new_lines"]) for g in result["glue"]], [("resolution-only", 0)])

    def test_a_glue_that_adds_a_line_found_nowhere_else_is_new_code(self):
        self.remote.glue("glue/1-2", [1, 2], {"a.txt": "one\ntwo\nbrand new behaviour\n"})
        self.sync([{"merge": "o/r#1"}, {"glue": "o/r:glue/1-2"}, {"merge": "o/r#2"}])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertTrue(result["ok"], "it reproduces; the point is that it is classified")
        self.assertEqual((result["glue"][0]["class"], result["glue"][0]["new_lines"], result["glue"][0]["files"]),
                         ("new code", 1, ["a.txt"]))


class Rebase(Case):
    def test_a_rebase_step_applies_the_prs_commits_onto_the_tip(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.remote.pr(2, "two.txt", "2\n")
        self.sync([{"merge": "o/r#1"}, {"rebase": {"pr": "o/r#2"}}])
        self.assertTrue((self.clone / "two.txt").exists())
        self.assertTrue(integration.verify(self.clone, "integration/x", self.url)["ok"])

    def test_a_rebase_that_does_not_apply_cleanly_is_refused(self):
        self.remote.pr(1, "a.txt", "one\n")
        self.remote.pr(2, "a.txt", "two\n")
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "o/r#1"}, {"rebase": {"pr": "o/r#2"}}])
        self.assertIn("glue step", str(cm.exception))


class Report(Case):
    def test_report_comes_from_the_plan_and_needs_no_api(self):
        h = self.remote.pr(1, "one.txt", "1\n")
        self.sync([{"merge": "o/r#1"}])
        rep = integration.report(self.clone, "integration/x", with_api=False)
        self.assertEqual(integration.summary(rep), f"o/r#1@{h[:9]}")
        self.assertEqual(rep["steps"][0]["kind"], "merge")

    def test_legacy_verify_still_flags_a_plain_commit(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.clone_run("checkout", "-q", "-b", "integration/old", "origin/main")
        self.clone_run("fetch", "-q", str(self.remote.dir), "+pull/1/head:refs/p1")
        self.clone_run("merge", "-q", "--no-ff", "-m", "Merge pr 1", "refs/p1")
        self.assertEqual(integration.legacy_problems(self.clone, "integration/old", "origin/main"), [])
        self.commit_on_branch("patch.txt", "x\n")
        self.assertEqual(len(integration.legacy_problems(self.clone, "integration/old", "origin/main")), 1)

    def clone_run(self, *a):
        subprocess.run(["git", "-C", str(self.clone), *a], check=True, env=ENV, capture_output=True)


class NoIdentity(Case):
    """A CI runner has no git identity and a developer's config may sign: neither may break or stall a replay."""

    def test_sync_and_verify_work_without_any_git_identity_or_signing_config(self):
        self.remote.pr(1, "one.txt", "1\n")
        self.remote.pr(2, "two.txt", "2\n")
        gone = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(("GIT_AUTHOR", "GIT_COMMITTER", "EMAIL"))}
        home = tempfile.mkdtemp()
        env_keep = {k: os.environ.get(k) for k in ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
        os.environ.update({"HOME": home, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        try:
            self.sync([{"merge": "o/r#1"}, {"merge": "o/r#2"}])
            self.assertTrue(integration.verify(self.clone, "integration/x", self.url)["ok"])
        finally:
            os.environ.update(gone)
            for k, v in env_keep.items():
                os.environ[k] = v

    def test_a_failure_that_is_not_a_conflict_is_not_called_one(self):
        self.remote.pr(1, "one.txt", "1\n")
        hooks = self.clone / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-merge-commit").write_text("#!/bin/sh\necho refused by a hook >&2\nexit 1\n")
        (hooks / "pre-merge-commit").chmod(0o755)
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "o/r#1"}])
        self.assertIn("failed", str(cm.exception))
        self.assertNotIn("conflicts with", str(cm.exception))


class PlanV2(Case):
    """Branch sources, pick steps and the version gate."""

    def plan(self, steps, ephemeral=False, **extra):
        return {"version": 2, "base": {"repo": "o/r", "ref": "main"}, "ephemeral": ephemeral, "steps": steps, **extra}

    def test_a_branch_of_a_fork_is_a_source_and_verifies(self):
        head = self.remote.branch("fork/feature", [("f.txt", "f\n")])[-1]
        locked = self.sync([{"merge": "f/r:fork/feature"}])
        self.assertEqual(locked["locked"]["steps"][0]["head"], head)
        self.assertTrue((self.clone / "f.txt").exists())
        self.assertTrue(integration.verify(self.clone, "integration/x", self.url)["ok"])

    def test_a_branch_source_can_be_pinned_and_a_moved_one_is_refused(self):
        self.remote.branch("fork/feature", [("f.txt", "f\n")])
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "f/r:fork/feature@" + "0" * 9}])
        self.assertIn("has moved", str(cm.exception))

    def test_a_pick_takes_exactly_the_named_commits_in_order(self):
        c1, c2, c3 = self.remote.branch("fix/logs", [("one.txt", "1\n"), ("two.txt", "2\n"), ("three.txt", "3\n")])
        locked = self.sync([{"pick": {"from": "o/r:fix/logs", "commits": [c1[:9], c3]}}])
        self.assertEqual(locked["locked"]["steps"][0]["commits"], [c1, c3], "full shas are locked")
        self.assertTrue((self.clone / "one.txt").exists() and (self.clone / "three.txt").exists())
        self.assertFalse((self.clone / "two.txt").exists(), "only the named commits")
        self.assertTrue(integration.verify(self.clone, "integration/x", self.url)["ok"])

    def test_a_pick_of_a_commit_the_source_does_not_contain_is_refused(self):
        self.remote.branch("fix/logs", [("one.txt", "1\n")])
        other = self.remote.branch("other", [("o.txt", "o\n")])[0]
        self.clone_run("fetch", "-q", str(self.remote.dir), "other")
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"pick": {"from": "o/r:fix/logs", "commits": [other]}}])
        self.assertIn("not reachable", str(cm.exception))

    def test_a_pick_that_conflicts_is_refused_and_asks_for_glue(self):
        self.remote.pr(1, "a.txt", "one\n")
        c = self.remote.branch("fix/logs", [("a.txt", "two\n")])[0]
        with self.assertRaises(SystemExit) as cm:
            self.sync([{"merge": "o/r#1"}, {"pick": {"from": "o/r:fix/logs", "commits": [c]}}])
        self.assertIn("glue step", str(cm.exception))

    def test_verify_lists_the_picked_commits_for_review(self):
        c1, c2 = self.remote.branch("fix/logs", [("one.txt", "1\n"), ("two.txt", "2\n")])
        self.sync([{"pick": {"from": "o/r:fix/logs", "commits": [c1, c2]}}])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertEqual([(p["source"], [c["sha"] for c in p["commits"]], [c["subject"] for c in p["commits"]]) for p in result["picks"]],
                         [("o/r:fix/logs", [c1, c2], ["fix/logs: one.txt", "fix/logs: two.txt"])])

    def test_verify_replays_the_locked_commits_even_if_the_source_has_moved(self):
        c = self.remote.branch("fix/logs", [("one.txt", "1\n")])[0]
        self.sync([{"pick": {"from": "o/r:fix/logs", "commits": [c]}}])
        self.remote.git("checkout", "-q", "fix/logs")
        self.remote.write("one.txt", "rewritten\n")
        self.remote.commit("later work")
        self.remote.git("checkout", "-q", "main")
        self.assertTrue(integration.verify(self.clone, "integration/x", self.url)["ok"])

    def test_a_pick_counts_as_a_known_source_for_glue_novelty(self):
        self.remote.pr(1, "a.txt", "one\n")
        c = self.remote.branch("fix/a", [("a.txt", "two\n")])[0]
        self.remote.git("checkout", "-q", "-B", "glue/1-fix", "pr1")
        subprocess.run(["git", "-C", str(self.remote.dir), "merge", "--no-commit", "--no-ff", "fix/a"], capture_output=True, env=ENV)
        self.remote.write("a.txt", "one\ntwo\n")
        self.remote.git("add", "-A")
        self.remote.git("commit", "-q", "-m", "resolve")
        self.remote.git("checkout", "-q", "main")
        self.sync([{"merge": "o/r#1"}, {"glue": "o/r:glue/1-fix"}, {"merge": "o/r:fix/a"}])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["glue"][0]["class"], "resolution-only", "'two' exists on the fix branch, a source of the plan")
        self.assertTrue(c)

    def test_a_source_built_on_top_of_the_glue_cannot_vouch_for_its_lines(self):
        """A pick source that descends from the glue branch contains every glue line: it must not make them 'known'."""
        self.remote.pr(1, "a.txt", "one\n")
        self.remote.pr(2, "a.txt", "two\n")
        self.remote.glue("glue/1-2", [1, 2], {"a.txt": "one\ntwo\nbrand new behaviour\n"})
        self.remote.git("checkout", "-q", "-B", "fix/later", "glue/1-2")
        self.remote.write("later.txt", "later\n")
        c = self.remote.commit("later work on top of the glue")
        self.remote.git("checkout", "-q", "main")
        self.sync([{"merge": "o/r#1"}, {"glue": "o/r:glue/1-2"}, {"merge": "o/r#2"}, {"pick": {"from": "o/r:fix/later", "commits": [c]}}])
        result = integration.verify(self.clone, "integration/x", self.url)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual((result["glue"][0]["class"], result["glue"][0]["new_lines"]), ("new code", 1))

    def test_a_version_1_plan_refuses_the_new_features(self):
        for step in ({"pick": {"from": "o/r:b", "commits": ["abc1234"]}}, {"merge": "o/r:fork/feature"}):
            with self.assertRaises(SystemExit) as cm:
                integration.load_plan(integration.dump_plan({"version": 1, "base": {"repo": "o/r", "ref": "main"}, "steps": [step]}))
            self.assertIn("version: 2", str(cm.exception))
        with self.assertRaises(SystemExit):
            integration.load_plan(integration.dump_plan({"version": 1, "base": {"repo": "o/r", "ref": "main"},
                                                         "steps": [{"merge": "o/r#1"}], "builds": [{"name": "x", "kind": "docker", "image": "o/x"}]}))

    def test_report_names_picks_and_branch_sources_without_the_api(self):
        c = self.remote.branch("fix/logs", [("one.txt", "1\n")])[0]
        self.sync([{"merge": "o/r:fix/logs"}, {"pick": {"from": "o/r:fix/logs", "commits": [c]}}])
        rep = integration.report(self.clone, "integration/x", with_api=False)
        self.assertEqual([(s["kind"], s["source"]) for s in rep["steps"]], [("merge", "branch"), ("pick", "branch")])
        self.assertEqual(rep["steps"][1]["commits"], [c[:9]])
        self.assertIn(":fix/logs@", integration.summary(rep))

    def clone_run(self, *a):
        subprocess.run(["git", "-C", str(self.clone), *a], check=True, env=ENV, capture_output=True)


class Builds(unittest.TestCase):
    def parse(self, builds):
        return integration.parse_builds({"builds": builds})

    def test_a_docker_build_gets_its_defaults(self):
        (b,) = self.parse([{"name": "apigw", "kind": "docker", "image": "sirosfoundation/integration/vc-apigw", "build_args": {"SERVICE_NAME": "apigw"}}])
        self.assertEqual((b["dockerfile"], b["context"], b["target"], b["platforms"], b["build_args"]),
                         ("./Dockerfile", ".", "", "linux/amd64", ["SERVICE_NAME=apigw"]))

    def test_an_artifact_build_is_a_command_and_its_output_globs(self):
        (b,) = self.parse([{"name": "sdk", "kind": "artifact", "run": "./gradlew assembleRelease", "outputs": ["**/build/outputs/aar/*.aar"],
                            "env": {"JAVA_VERSION": 17}}])
        self.assertEqual((b["runner"], b["env"]), ("ubuntu-latest", {"JAVA_VERSION": "17"}))

    def test_bad_builds_are_refused(self):
        bad = [
            [{"name": "a", "kind": "oci"}],                                                   # unknown kind
            [{"name": "A!", "kind": "docker", "image": "o/x"}],                              # bad name
            [{"name": "a", "kind": "docker", "image": "o/x"}, {"name": "a", "kind": "docker", "image": "o/y"}],   # duplicate
            [{"name": "a", "kind": "docker", "image": "ghcr.io/o/x:tag"}],                    # registry and tag belong to the workflow
            [{"name": "a", "kind": "docker", "image": "o/x", "run": "rm -rf /"}],             # a key of the other kind
            [{"name": "a", "kind": "docker", "image": "o/x", "build_args": ["NOEQUALS"]}],
            [{"name": "a", "kind": "artifact", "run": "make", "outputs": []}],
            [{"name": "a", "kind": "artifact", "run": "make", "outputs": ["/etc/passwd"]}],  # outputs stay inside the checkout
            [{"name": "a", "kind": "artifact", "run": "make", "outputs": ["../x"]}],
            [{"name": "a", "kind": "artifact", "run": "  ", "outputs": ["x"]}],
            [{"name": "a", "kind": "artifact", "run": "make", "outputs": ["x"], "env": {"BAD NAME": "1"}}],
            [{"name": "a", "kind": "artifact", "run": "make", "outputs": ["x"], "runner": "self-hosted"}],   # hosted runners only
        ]
        for builds in bad:
            with self.assertRaises(SystemExit, msg=str(builds)):
                self.parse(builds)

    def test_the_builds_command_prints_a_matrix_split_by_kind(self):
        remote = Remote()
        clone = Path(tempfile.mkdtemp())
        subprocess.run(["git", "clone", "-q", str(remote.dir), str(clone)], check=True, env=ENV)
        remote.pr(1, "one.txt", "1\n")
        plan = {"version": 2, "base": {"repo": "o/r", "ref": "main"}, "steps": [{"merge": "o/r#1"}],
                "builds": [{"name": "svc", "kind": "docker", "image": "o/x"}, {"name": "sdk", "kind": "artifact", "run": "make", "outputs": ["out/*"]}]}
        integration.sync(clone, "integration/x", plan, lambda repo: str(remote.dir))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            integration.main(["builds", "--path", str(clone), "--tip", "integration/x"])
        import json
        got = json.loads(buf.getvalue())
        self.assertEqual(([b["name"] for b in got["docker"]], [b["name"] for b in got["artifact"]]), (["svc"], ["sdk"]))

    def test_remotes_maps_owner_repo_to_a_local_directory(self):
        base = Path(tempfile.mkdtemp())
        remote = Remote()
        (base / "o").mkdir()
        (base / "o" / "r").symlink_to(remote.dir)
        remote.pr(1, "one.txt", "1\n")
        clone = Path(tempfile.mkdtemp())
        subprocess.run(["git", "clone", "-q", str(remote.dir), str(clone)], check=True, env=ENV)
        plan = Path(tempfile.mkdtemp()) / "plan.yaml"
        plan.write_text(integration.dump_plan({"version": 2, "base": {"repo": "o/r", "ref": "main"}, "steps": [{"merge": "o/r#1"}]}))
        self.assertEqual(integration.main(["sync", "--path", str(clone), "--branch", "integration/x", "--plan", str(plan), "--remotes", str(base)]), 0)
        self.assertEqual(integration.main(["verify", "--path", str(clone), "--tip", "integration/x", "--remotes", str(base)]), 0)



if __name__ == "__main__":
    unittest.main()
