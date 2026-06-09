# VEX (Vulnerability Exploitability Exchange)

This directory contains OpenVEX documents used to filter Trivy CVE scan results
across the sirosfoundation GitHub org.

## How it works

The reusable Docker build workflow (in this repo) downloads the org-level VEX
document during Trivy scans and uses it to filter out CVEs that have been
assessed as not affecting our services.

### Two-tier model

1. **Org-level** (`vex/org.openvex.json` in this repo) — applies to all repos
   using the reusable Docker workflow. Contains assessments for OS-level
   packages in our standard base images that are never invoked by Go binaries.

2. **Repo-level** (`.trivyvex.openvex.json` at repo root) — per-repo overrides
   for service-specific assessments. These take priority over the org-level
   document.

### Priority

When both exist, Trivy evaluates both. The repo-level VEX is listed after the
org-level VEX, so repo-level statements take priority.

## Format

All VEX documents use the [OpenVEX](https://github.com/openvex/spec) format.
Products are identified by [PURL](https://github.com/package-url/purl-spec)
(Package URL). A PURL without a version matches all versions of that package.

## Adding a new VEX statement

1. Identify the CVE and affected OS package from the Trivy SARIF results.
2. Verify the vulnerable code path is not reachable from the Go binary.
3. Add a statement to `org.openvex.json` (if it applies org-wide) or the
   repo's `.trivyvex.openvex.json` (if repo-specific).
4. Use one of the valid `justification` values:
   - `component_not_present` — the package is not in the image
   - `vulnerable_code_not_reachable` — the code path is never called
   - `vulnerable_code_cannot_be_controlled_by_adversary` — no attacker input reaches it
   - `inline_mitigations_already_exist` — other controls prevent exploitation
5. Commit, push, and the next Trivy scan will filter the CVE.

## Review cadence

VEX statements should be reviewed:
- When base images are updated (Alpine/Debian version bumps)
- When new CRITICAL/HIGH CVEs appear in the Security tab
- Quarterly as part of the compliance review cycle
