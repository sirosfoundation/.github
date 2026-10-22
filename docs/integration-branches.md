# Integration branches: a documented, reproducible "bag of PRs"

We often test a build that combines several unmerged pull requests. The rule that keeps such a build honest:

> An `integration/<name>` branch is **generated from a plan** and contains nothing else: no hand edits, no patch commits, no
> change hidden in a merge commit. Anything needed to make PRs fit together lives on a visible **glue branch** named in the plan.

The plan is `.integration/plan.yaml` in the branch itself, so the branch is self-describing and an ad-hoc test needs no central
registry. `actions/integration/integration.py` creates, verifies and reports on branches; `integration-build.yml` builds them.

## The plan

```yaml
version: 1
base: {repo: SUNET/vc, ref: main}
ephemeral: false            # true: an ad-hoc test (reserved for the comments follow-up)
steps:
  - merge: SUNET/vc#839                       # merge the PR head (--no-ff); owner/repo#N[@sha] (the sha pins the head)
  - glue:  {branch: sirosfoundation/vc:glue/839-831, note: resolves the seed_recover conflict}
  - merge: SUNET/vc#831
  - rebase: {pr: SUNET/vc#832}                # the PR's own commits cherry-picked onto the tip (must apply cleanly)
locked:                                       # written by `sync`: exactly what was built
  base_sha: <40 hex>
  steps: [{head: <40 hex>}, ...]
```

`sync` fetches every head, replays the steps, writes `locked` and commits the plan as the last commit. `verify` replays the
**locked** heads (so a PR that has moved since does not change the answer) and requires the branch's tree, ignoring `.integration/`,
to be identical. Any hand edit, patch commit, hand-resolved conflict or change smuggled into a merge shows up as a tree difference.

## Conflicts and glue

A conflict is never resolved inside a merge. It is resolved on a **glue branch** (a normal branch in one of our repos that merges
the conflicting PRs and commits the resolution), and the plan names it in a `glue` step before the PR it conflicts with.
`verify` classifies every glue branch:

- **resolution-only**: every added line already exists in the tip before it, the base or one of the plan's PR heads. It only
  chooses between existing code, so it needs no review and no PR. It is reported as "no new lines".
- **new code**: it adds lines found nowhere else. It is a change like any other: give it a draft PR and have it reviewed.
  `verify` prints `glue <branch>: new code (N new lines in <files>)`. A plan marked `ephemeral: true` may skip the PR.

## Using it

```sh
# create or update a branch from a plan (a re-lock at the latest heads: --from <branch>)
python3 actions/integration/integration.py sync --path <clone> --branch integration/my-test --plan plan.yaml [--push origin]

python3 actions/integration/integration.py verify --path <clone> --tip integration/my-test
python3 actions/integration/integration.py report --path <clone> --tip integration/my-test [--json|--summary]
```

Add the workflow template **Integration branch (bag of PRs)** to a repository (Actions -> New workflow). On every push to
`integration/**` it:
1. runs `verify` (a branch that fails is not built);
2. builds and pushes `ghcr.io/<image-name>:<branch>-<sha7>` via `docker-build-push.yml` (Buildx, cache, Trivy); the digest is in the run output.

Cross-reference comments on the participating PRs are a planned follow-up; this version does not post any.

## Rules for consumers
These images contain UNMERGED code. Never use `latest`; pin by **digest** (the build summary prints it); re-pin to a release when the PRs
land. `report --json` gives each PR's state and whether its head moved since the build (the branch is then stale).

## Limits (v1)
- A rebase step needs its locked head to still be fetchable (a force-pushed-away PR head cannot be replayed); merge and glue steps always can,
  because their heads are in the branch's own history.
- Fetching other repositories' PR heads is over https: public repositories only.
- Branches created before this existed have no plan: `verify --legacy` checks them the old way (only merges, none hand-edited) and
  the first run usually shows plain commits and hand-resolved conflicts. Regenerate them from a plan.
