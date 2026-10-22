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




if __name__ == "__main__":
    unittest.main()
