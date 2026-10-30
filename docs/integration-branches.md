# Integration branches: a documented, reproducible "bag of changes"

We often test a build that combines several unmerged changes: pull requests, a fork's feature branch, a fix commit that has no PR yet. The rule that keeps such a build honest:

> An `integration/<name>` branch is **generated from a plan** and contains nothing else: no hand edits, no patch commits, no
> change hidden in a merge commit. Anything needed to make PRs fit together lives on a visible **glue branch** named in the plan.

The plan is `.integration/plan.yaml` in the branch itself, so the branch is self-describing and an ad-hoc test needs no central
registry. `actions/integration/integration.py` creates, verifies and reports on branches; `integration-build.yml` builds them.

## The plan

```yaml
version: 2
base: {repo: SUNET/vc, ref: main}           # a branch, or a full 40-hex commit
ephemeral: false            # true: an ad-hoc test (reserved for the comments follow-up)
steps:                      # a SOURCE is a PR `owner/repo#N[@sha]` or a branch `owner/repo:branch[@sha]` (the sha pins the head)
  - merge: SUNET/vc#839                       # merge the source's head (--no-ff)
  - glue:  {branch: sirosfoundation/vc:glue/839-831, note: resolves the seed_recover conflict}
  - merge: SUNET/vc#831
  - merge: sirosfoundation/vc:docs/operator   # a fork's branch is a source like a PR
  - rebase: SUNET/vc#832                      # the source's own commits cherry-picked onto the tip (must apply cleanly)
  - pick: {from: sirosfoundation/vc:fix/logs, commits: [5da879895]}   # exactly these commits, which must be reachable from the source
builds:                                       # what is built from the branch (below); optional
  - {name: apigw, kind: docker, image: sirosfoundation/integration/vc-apigw, build_args: [SERVICE_NAME=apigw]}
  - {name: sdk, kind: artifact, runner: ubuntu-latest, run: ./gradlew assembleRelease, outputs: ['**/build/outputs/aar/*.aar']}
locked:                                       # written by `sync`: exactly what was built
  base_sha: <40 hex>
  steps: [{head: <40 hex>}, {head: <40 hex>, commits: [<40 hex>]}, ...]
```

Version 1 plans (PR merges and rebases, glue) stay valid; `pick`, branch sources and `builds` need `version: 2`.

`sync` fetches every head, replays the steps, writes `locked` and commits the plan as the last commit. `verify` replays the
**locked** heads (so a PR that has moved since does not change the answer) and requires the branch's tree, ignoring `.integration/`,
to be identical. Any hand edit, patch commit, hand-resolved conflict or change smuggled into a merge shows up as a tree difference.

## What is built: `builds`

The plan says what the branch is built into. The tool and the workflow know nothing about any deployment: an environment consumes the
results by digest or checksum, and anything it layers on top (a baked-in data file, a config) is its own concern.

- `kind: docker`: `image` (a GHCR path, no registry or tag; the workflow tags `<branch>-<sha7>`), `dockerfile`, `context`, `target`,
  `build_args` (`KEY=VALUE` list or mapping), `platforms`. Built and pushed through `docker-build-push.yml` (Buildx, cache, Trivy).
- `kind: artifact`: `run` (the shell commands), `outputs` (relative globs of the files they produce), `runner` (a GitHub-hosted
  one: `ubuntu-*`, `macos-*`, `windows-latest`), `env`. This is how SDK and native-app builds are expressed: a Gradle AAR or sample
  APK, a Swift XCFramework on a macOS runner, an npm or Rust package. The files are uploaded as a workflow artifact with their
  sha256 in the job summary. Publishing to a package registry (Maven, a Swift package index, npm) is a later `publish:` option.

`builds --tip B` prints the validated targets as JSON; the reusable workflow turns them into one matrix job per target. An `artifact`
build runs the plan's commands in the branch's checkout with read-only permissions and no secrets: the same trust as workflow code
pushed to the branch. A plan without `builds:` builds one image from the workflow inputs, as before.

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

## Test targets

Where the concept is exercised first (one per kind of result, from small to hard):
- **go-trust** (Go, one Dockerfile, small): the smallest end to end check of plan -> verify -> image.
- **go-wallet-backend** (Go, the wallet engine): a plan of `main` + open PRs + the local test and rules commits (a `new code` glue that
  needs its draft PR), image per plan.
- **vc** (a fork of SUNET/vc; Go + Rust ZK libraries): the hard one: forks' branches, cherry-picked fixes, conflicts resolved on glue
  branches, several service images and a verifier target that takes the ZK libraries by SHA as build arguments.
- **siros-sdk-kotlin** (Gradle): an `artifact` build producing the AAR and the sample app's APK; **siros-sdk-swift**: an XCFramework on a
  macOS runner (needs a `macos-*` runner and the Xcode version its CI pins).

## Limits
- A rebase or pick step needs its locked head to still be fetchable (a force-pushed-away PR head cannot be replayed); merge and glue steps
  always can, because their heads are in the branch's own history.
- A source that exists only on someone's machine cannot be built in CI: push it to a branch of one of our repositories first (a pick of a
  commit on a fork's branch, a glue branch). `--remotes DIR` replays plans against local repositories for the same reason in tests.
- Fetching other repositories' PR heads is over https: public repositories only.
- Branches created before this existed have no plan: `verify --legacy` checks them the old way (only merges, none hand-edited) and
  the first run usually shows plain commits and hand-resolved conflicts. Regenerate them from a plan.
