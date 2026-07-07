# SIROS Ecosystem — GitHub Copilot Instructions

These instructions apply to all repositories in the **sirosfoundation** GitHub organisation.
Individual repositories may extend or override specific sections with a repo-level
`.github/copilot-instructions.md`.

---

## 1. Ecosystem Overview

SIROS is a production digital-identity wallet platform. The core components are:

| Repo | Role |
|------|------|
| `go-wallet-backend` | Wallet backend: OID4VCI/VP engine, WebSocket API, storage |
| `go-trust` | AuthZEN Trust PDP: ETSI TSL/LoTE, OpenID Federation, DID Web |
| `go-spocp` | SPOCP rule-based ACL engine with AuthZEN API |
| `go-wmp` | Go library for the Wallet Messaging Protocol (WMP) |
| `go-cryptoutil` | Extensible crypto utilities; brainpool, algorithm mapping |
| `go-wallet-as` | Standalone AuthServer (WebAuthn/passkey, CSC, r2ps) |
| `wmp` | WMP specification, JSON Schema, test vectors |
| `siros-sdk-kotlin` | Android SDK (transport, auth, keystore, flow, credentials) |
| `siros-sdk-swift` | iOS/macOS SDK (same module structure as Kotlin) |
| `wallet-frontend` | Reference web wallet (React/TypeScript); the reference implementation |
| `siros-conformance` | OpenID Foundation Conformance Suite test harness |
| `privatedata-spec` | Cross-client private data format spec and test vectors |
| `go-grc` | GRC CLI: compliance-as-code for EUDI/ISO 27001/GDPR |
| `compliance` | Control catalog, framework mappings, audit findings |
| `go-r2ps-service` | Remote WSCD / r2ps signing service |
| `go-oidf-ta` | OpenID Federation Trust Anchor service |
| `wallet-companion` | Browser extension: DC API bridge, wallet auto-registration, protocol-aware routing |
| `siros-wscd-manager` | Rust WSCD Manager: pluggable key management for all clients (softkey/r2ps/fido2) |
| `sirosid-dev` | Local Docker Compose development environment |

> **Never reference or suggest usage of `wallet-backend-server`** — it is deprecated and
> superseded by `go-wallet-backend`. Also exclude: `wallet-e2e-tests` (archived),
> `mtcvctm` (archived).

---

## 2. Cryptography — Rely on Well-Tested Libraries

**Never implement cryptographic primitives from scratch.**

All cryptographic operations MUST use established, well-audited libraries:

### Go
- Password hashing: `golang.org/x/crypto/bcrypt`
- JWT signing/verification: `github.com/golang-jwt/jwt/v5` and `github.com/go-jose/go-jose/v4`
- JWK operations: `github.com/lestrrat-go/jwx/v3`
- WebAuthn/FIDO2: `github.com/go-webauthn/webauthn`
- Certificate parsing with non-standard curves (brainpool, PQ): `github.com/sirosfoundation/go-cryptoutil`
  and its `brainpool` sub-module — do NOT fork `crypto/x509`
- ECDSA format conversion (IEEE P1363 ↔ ASN.1 DER): `go-cryptoutil.ECDSARawToASN1` / `ECDSAASN1ToRaw`

### Kotlin (Android SDK)
- Key derivation: `javax.crypto` HKDF-SHA256 via Android Keystore or BouncyCastle
- JWE encryption: `com.nimbusds:nimbus-jose-jwt`
- WebAuthn/FIDO2: Android Credential Manager API (platform passkeys)

### Swift (iOS/macOS SDK)
- Key derivation: `CryptoKit.HKDF`; guard against `#if canImport(CryptoKit)`
- JWE encryption: AES-GCM via `CryptoKit`; wrapped in `EncryptedContainer`
- WebAuthn/FIDO2: `ASAuthorizationController` (platform passkeys)

### TypeScript / JavaScript
- Use WebCrypto API (`crypto.subtle`) — never third-party primitive implementations
- JWT: `jose` library only
- DID operations: align with the `dc4eu/vc` project utilities

### Rust
- Use `ring` or `rustls` for TLS/AEAD; `p256` crate for ECDSA
- Never implement elliptic curve arithmetic manually
- FIDO2/CTAP2: the `soft-fido2` workspace crates

**Algorithm mapping across protocols (JWS / COSE / XML-DSIG) must be centralised.**
In Go, use `go-cryptoutil.AlgorithmRegistry`. Do not scatter algorithm constants across packages.

### Key operations (signing, key generation, key attestation)
- All client-side key operations go through `siros-wscd-manager` (`WscdManager` + plugin).
  Never call `crypto.subtle`, `CryptoKit`, or Android Keystore directly for wallet keys.
- In wallet-frontend: use `WalletSigner` / `KeystoreSignerAdapter` (WSCD WASM bridge).
- In Kotlin/Swift SDKs: call the UniFFI-generated `WscdManager` bindings.
- `SecurityProperties` (`key_storage`, `user_authentication`, `certification`, `amr`) are
  reported by the plugin and must be forwarded in key-attestation requests — never hardcoded.

---

## 3. Authentication — WebAuthn / FIDO2 Only

- **WebAuthn/FIDO2 passkeys are the sole user authentication mechanism.**
  Do not introduce password-based, OTP, or SMS flows.
- The **FIDO2 PRF extension** drives key derivation for encrypted credential storage:
  `PRF output → HKDF-SHA256 → AES-256 main key → JWE credential containers`.
- Use the **FIDO2 raw signature extension** for key binding proofs as it becomes
  available on platforms.
- WebAuthn RP-ID is a **plain hostname**, never a URL with scheme or port.
- The Android APK key hash for `assetlinks.json` must be derived dynamically from the
  debug/release keystore — never hardcoded.

---

## 4. Trust — Always Delegate to AuthZEN PDP

**There is no local trust evaluation anywhere in the stack.**

All trust decisions MUST flow through the AuthZEN PDP (`go-trust`):

```
Client or backend → POST /evaluation → go-trust PDP → decision: true/false
```

Rules:
- Configure the PDP endpoint via `trust.default_endpoint` (global) or
  `trust_config.pdp_url` (per-tenant JWT claim).
- Development/testing: use `static:always-trusted` or `static:never-trusted`
  registries — never ship these in production configs.
- The transport does not matter: engine/WebSocket, HTTP proxy, and direct flows
  all evaluate trust the same way.
- Never validate X.509 chains against system roots as a trust decision.
  That is `go-trust`'s job via ETSI TSL validation.

`go-trust` supports: ETSI TS 119 612 (TSL), ETSI TS 119 602 (LoTE),
OpenID Federation, DID Web, and whitelist registries. Add support for new
trust frameworks by extending `go-trust`, not by implementing local checks.

---

## 5. ACL / Policy — SPOCP for Rule-Based Decisions

- For general access-control decisions beyond trust evaluation, use `go-spocp`.
- SPOCP rules are S-expressions (canonical format). Define rules in a `.spocp` file;
  do not embed rule strings as Go string literals scattered through code.
- Access the SPOCP engine via its **AuthZEN API** (`POST /access/v1/evaluation`)
  for uniformity with go-trust.
- The adaptive engine in go-spocp automatically optimises for ruleset shape —
  use it instead of the manual engine unless you have a specific performance reason.

---

## 6. Transport Independence — WMP and Native WebSocket

The core wallet protocol MUST remain transport-independent:

| Transport | Use Case |
|-----------|----------|
| Native WebSocket | Browser wallet-frontend and direct SDK connections |
| WMP WebSocket (`wmp.v1` subprotocol) | Multi-party messaging, agent-to-agent |
| HTTP + Server-Sent Events (SSE) | Firewalled / proxy environments |

Implementation rules:
- **Go (go-wmp)**: implement `wmp.Handler` interface; use `ws.Upgrade` for WebSocket
  or `httpsse.Handler` for HTTP+SSE. Never assume a specific transport in handler logic.
- **Kotlin SDK**: `sdk:transport` module is the single integration point.
  `WmpWebSocketTransport` and `WmpHttpSseTransport` both implement the same interface.
- **Swift SDK**: `SirosTransport` module; `WebSocketTransport` and `HttpSseTransport`.
- WMP message format: JSON-RPC 2.0 with `wmp.*` method namespace
  (`wmp.session.create`, `wmp.flow.start`, `wmp.flow.action`, etc.).
- WebSocket keepalive: server pings every **30 s**, pong timeout **10 s**.
  This keeps connections alive through load-balancer idle timeouts.

---

## 7. Privacy-Preserving Architecture

**Principle: the server/engine learns as little as possible about user credentials.**

Mandatory data minimisation rules:

1. **Keys never leave the client.** Signing requests (`sign_request`) are sent from the
   engine to the client; the client signs locally and returns only the signature.
2. **Client-side credential matching.** The engine sends a `presentation_definition`;
   the client matches its local credential inventory and returns only the selected
   credential(s). The engine never receives the full credential list.
3. **Log sanitisation.** Server-side logs MUST NOT contain: credential content,
   presentation nonces, private key material, PRF outputs, or user biometrics.
4. **Local engine mode.** Native apps (iOS, Android) SHOULD support embedding the
   engine so that no credential-flow data reaches the cloud.
5. **Encrypted storage.** Credentials are stored as JWE-encrypted containers
   (AES-256-GCM). The decryption key is derived from WebAuthn PRF; the backend
   never sees unencrypted credential content.
6. **Minimal JWT claims.** Tokens carry only what downstream services need.
   Do not add convenience claims that expose user attributes unnecessarily.

Engine visibility table (do not violate):

| Engine SEES | Engine MUST NOT SEE |
|-------------|---------------------|
| Flow type (OID4VCI / OID4VP) | Full credential inventory |
| Issuer / verifier URL | Credential content (except during sign) |
| Presentation definition | Private keys |
| Trust evaluation result | Decrypted credential store |
| One credential at signing time | User password / biometrics |

---

## 8. EUDI Compliance and Multi-Protocol Support

### Primary standards
- **OID4VCI** (OpenID for Verifiable Credential Issuance) — pre-authorised and
  authorization-code grants, deferred issuance, immediate issuance.
- **OID4VP** (OpenID for Verifiable Presentations) — direct_post, HAIP profile.
- **SD-JWT VC** — selective disclosure JWT-based verifiable credentials.
- **mdoc / ISO 18013-5** — mobile driving licence / mDoc credential format.
- **DCQL** (Digital Credentials Query Language) — credential selection on client.

### Normative references
- Local mirror in `compliance/standards/` (etsi/, ietf/, iso/, enisa/).
  Always cite from this mirror; do not fetch remote specs at runtime.
- ARF v2.8.0 is the primary EUDI reference; ETSI TS 119 612 / 119 602 for trust lists.
- EC Technical Specifications TS01–TS14 are normative for wallet certification.

### Evolving non-EUDI support
- **WMP** adds: eDelivery (SMP/BDXL discovery), ERDS delivery evidence, MLS E2E encryption.
- **OpenID Federation** for trust chains and Entity Statements.
- **DID Web / DID Key** for decentralised identifiers.
- New protocol support goes through WMP profiles or dedicated Go packages —
  do not patch OID4VCI/VP handlers for unrelated protocols.

### DPoP (RFC 9449)
- All token requests that support it MUST include DPoP proofs.
- Use an ephemeral per-session DPoP key pair; never reuse DPoP keys across sessions.

---

## 9. Multi-Client Feature Parity

Three clients serve a single `go-wallet-backend`. They MUST maintain feature parity:

| Client | Language | Auth | Transport | Key Backend |
|--------|----------|------|-----------|-------------|
| `wallet-frontend` | TypeScript / React | WebAuthn (browser) | Native WS + HTTP | WSCD WASM (`softkey`) |
| `siros-sdk-kotlin` | Kotlin / Android | Credential Manager | WS + HTTP+SSE | WSCD UniFFI |
| `siros-sdk-swift` | Swift / iOS/macOS | ASAuthorizationController | WS + HTTP+SSE | WSCD UniFFI |

Rules:
- **`wallet-frontend` is the reference implementation.** When in doubt about
  private-data format, credential selection, WebSocket protocol, or auth flow,
  match `wallet-frontend` behaviour exactly.
- New engine features (new WMP methods, new sign actions) require implementations
  in all three clients before the backend feature is considered complete.
- Cross-client private data format is governed by `privatedata-spec`.
  Any change to the format must update `test-vectors/` in that repo.
- The Android Passkey Provider Service (`sdk:passkey-provider`) surfaces SIROS
  credentials in the system credential picker — keep it in sync with `sdk:auth`.

---

## 10. Testing Requirements

### Coverage targets
| Component | Minimum |
|-----------|---------|
| Overall | 70% |
| Auth / crypto / storage / API handlers | 80% |
| go-spocp | 96% |
| go-trust | 80% |

### Rules
- All new code MUST include tests. PRs must not decrease overall coverage.
- Go unit tests use **in-memory storage** — never a real database.
- Go tests use `t.Context()` (Go 1.21+) for context management.
- Table-driven tests are preferred for parametric cases.
- Kotlin tests use JUnit5 + MockK; coroutine tests use `runTest {}`.
- Swift tests use `XCTest`; async tests use `async throws` functions.

### Conformance testing
- All issuer/verifier/wallet changes must pass `siros-conformance` before merge:
  `make test-issuer`, `make test-verifier`, `make test-wallet`.
- New credential format support requires test vectors in `sg-test-vectors`.
- Cross-client private data changes require passing `privatedata-spec/conformance/`.
- Run specific variants with `--grep` (e.g., `--grep "sd_jwt_vc"`) to avoid
  running the entire suite for targeted changes.

---

## 11. Go-Specific Conventions

### Language & style
- Go 1.21+ features are permitted (use `t.Context()`, `slices`, `maps` packages).
- Use `any` instead of `interface{}` everywhere (ADR-003).
- Prefer `errors.Is` / `errors.As` over type assertions for error checking.

### Error handling (ADR-007)
```go
// Sentinel errors at package level:
var ErrNotFound = errors.New("not found")

// Wrapping with context:
return fmt.Errorf("failed to resolve issuer metadata: %w", err)

// API layer translates to HTTP status:
if errors.Is(err, storage.ErrNotFound) {
    c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
    return
}
```

### Storage (ADR-006)
- All storage access via the `Store` interface. Never call a concrete backend directly
  from business logic.
- Memory backend for tests; SQLite for single-instance; MongoDB for production scaling.
- Add GORM tags + BSON tags + JSON tags to domain structs for portability.

### Caching (ADR-004)
- Use `github.com/jellydator/ttlcache/v3` for all TTL caching needs.
- TTL values must be configurable — never hardcoded durations in cache instantiation.
- Expose cache metrics via Prometheus.

### Configuration (ADR-008)
- YAML file + environment variable overrides (12-factor).
- Env var naming: `WALLET_<SECTION>_<KEY>` (upper-snake, no hyphens).
- Validate all config at startup — fail fast with a clear error message.
- Secrets ONLY via environment variables; never in YAML files committed to git.

### Logging
- Use `go.uber.org/zap` for structured logging.
- Log level `info` in production; `debug` available via config.
- **Never log**: credential content, nonces, private key material, PRF output,
  user PII beyond a pseudonymous ID.

### Project structure
```
cmd/         # Entry points (one binary per sub-command)
internal/    # Application-private packages
  api/       # HTTP handlers and routes
  domain/    # Business entities (storage-agnostic)
  engine/    # Protocol state machines
  service/   # Business logic / orchestration
  storage/   # Store interface + implementations
pkg/         # Exported, reusable packages
  config/    # Configuration types and loading
  trust/     # AuthZEN client wrapper
  logging/   # Logger construction
docs/adr/    # Architecture Decision Records
```

### DoS hardening
- Limit concurrent pending flows per session: `MaxPendingFlowsPerSession = 3`.
- Apply per-IP rate limiting on all public endpoints.
- Use circuit breakers for external calls (trust PDP, issuer metadata).

---

## 12. Multi-Tenancy (ADR-011)

- Tenant routing uses `X-Tenant-ID` HTTP header from frontend to backend.
- The JWT `tenant_id` claim is **authoritative** for authenticated requests.
- Frontend URL structure: `/id/:tenantId/...` for non-default tenants; `/...` for default.
- Tenant-specific trust PDP endpoint is propagated via `trust_endpoint` JWT claim.
- The default tenant is `"default"` — never `""` (empty string).

---

## 13. Security Hardening (OWASP Top 10)

- **Injection**: All database queries via ORM or parameterised statements.
  Never build query strings from user input.
- **Broken Access Control**: Every API endpoint validates the JWT `user_id` and
  `tenant_id` claims. Storage layer enforces tenant isolation.
- **Cryptographic Failures**: See §2. No custom crypto; TLS everywhere; no HTTP.
- **SSRF**: The engine HTTP client uses a dial restriction that blocks RFC 1918
  and loopback addresses. PDP HTTP client uses `NewPDPHTTPClient` which allows
  operator-configured private addresses.
- **Security Misconfiguration**: Validate config at startup; fail on unknown keys.
  `always-trusted` registry MUST NOT appear in production configs.
- **Vulnerable Components**: Dependencies kept up-to-date; Dependabot enabled org-wide.
- **Logging Failures**: Structured logs, no sensitive data (see §11 logging rules).
- **Server-Side Request Forgery**: Outbound HTTP client has explicit allowlist/blocklist.

---

## 14. Infrastructure & Cloud-Native

- Services are **stateless**; all state in external storage (MongoDB / SQLite / Redis).
- Kubernetes: expose `/healthz` (liveness) and `/readyz` (readiness) probes on every service.
  `go-trust` returns HTTP 503 on `/readyz` until TSLs are loaded.
- Prometheus metrics on `/metrics` for every service.
- Docker images published to `ghcr.io/sirosfoundation/<repo>`.
- Helm charts in `helm-charts/` repo — one chart per deployable service.
- 12-factor: config from env, logs to stdout, no local filesystem dependencies.
- Use `docker compose` (V2, no hyphen) in scripts and Makefile targets.

---

## 15. Compliance as Code

- All new platform features must be assessed against controls in `compliance/catalog/technical/`.
- Security findings are filed as GitHub issues and linked from `compliance/audits/`.
- Framework mappings (EUDI, ISO 27001, GDPR, OWASP ASVS) are in `compliance/mappings/`.
- Run `grc derive` and `grc render` after any control or finding change.
- Control IDs follow the pattern `SID-<DOMAIN>-<NN>` (e.g., `SID-AUTH-01`, `SID-CRYPTO-03`).

### AI-Assisted Compliance with go-grc MCP

`go-grc` ships an MCP server (`grc serve --mcp`) that exposes the full compliance
catalog to AI assistants (GitHub Copilot, Claude, etc.) as tools and resources.

**When working on compliance tasks, prefer using these MCP tools over manual file reads:**

| MCP tool / resource | When to use |
|---------------------|-------------|
| `search_controls` | Find controls matching a keyword or topic |
| `search_findings` | Locate open/closed audit findings |
| `compliance_gap_analysis` | Identify uncovered requirements for a framework |
| `control_coverage` | Check which frameworks reference a given `SID-*` control |
| `finding_statistics` | Get open/closed finding counts before drafting a fix |
| `risk_summary` | Review accepted risks before proposing mitigations |
| `list_architecture_docs` | Discover relevant architecture/security docs |
| `grc://catalog/control/{controlId}` | Full control detail including evidence links |
| `grc://audit/finding/{findingId}` | Finding detail including linked PRs and evidence |
| `grc://mapping/{frameworkId}` | Full framework ↔ control mapping |
| `grc://architecture/{document}` | Architecture or security document content |

**Workflow for AI-assisted compliance work:**

1. **Assess impact**: before implementing a feature, call `search_controls` to find
   relevant controls; call `compliance_gap_analysis` for the affected framework.
2. **Link evidence**: after implementing, add the PR URL or deployed endpoint as
   evidence to the relevant finding YAML in `compliance/audits/`.
3. **Derive and render**: run `grc derive && grc render` to recompute statuses and
   regenerate the compliance site.
4. **Sync findings**: run `grc sync` to push finding status changes back to GitHub issues.
5. **Export for auditors**: use `grc export` to produce a JSON evidence package.

**Never manually edit derived status fields** in control or mapping YAML —
they are overwritten by `grc derive`. Edit only source data (findings, evidence).

---

## 16. WSCD Manager — Key Management Abstraction

`siros-wscd-manager` is the **single source of truth for all wallet key operations** across
every client platform. It is a Rust crate that compiles to:
- **Native shared library** — consumed by the Kotlin and Swift SDKs via UniFFI bindings
  (`bindings/kotlin/`, `bindings/swift/`)
- **WASM module** — consumed by `wallet-frontend` via `wasm-bindgen` (feature flag `wasm`)

### Plugin model

| Plugin | Backend | Auth | Lifecycle |
|--------|---------|------|-----------|
| `softkey` | Software P-256 / Ed25519 JWE container | None | Not implemented |
| `r2ps` | Remote PKCS#11 HSM via R2PS | OPAQUE / WebAuthn | Fully implemented |
| `fido2` | CTAP2 rawSign (Yubico previewSign) | FIDO2 | Fully implemented |

### Rules when working in `siros-wscd-manager`

- **Add new backends as plugins** — implement `WscdPlugin` trait. Never add backend-specific
  code to `manager.rs`.
- **Auth callbacks** (`AuthCallback` trait) are always provided by the host (SDK / frontend).
  Plugins never prompt users directly and never store auth material.
- **Progress callbacks** (`ProgressCallback` trait) push async status to the host UI
  (spinners, step indicators). Always call them around long-running operations.
- **Zeroize on drop**: all types holding private key bytes must `#[derive(Zeroize, ZeroizeOnDrop)]`.
  Never copy raw key bytes into a non-zeroizing buffer.
- **`SecurityProperties`** must be populated by every plugin for every key
  (`key_storage`, `user_authentication`, `certification`, `amr` per CS-04 §7.1.3).
  These flow into key-attestation JWT claims — never hardcode them at the call site.
- Feature flags: `plugin-r2ps` and `plugin-fido2` are off by default; `wasm` enables the
  WASM target. CI must test at minimum `--features plugin-r2ps,plugin-fido2`.
- **Plugin resolution order**: per-key `plugin_id` binding → per-operation default →
  global default. Do not bypass this order.
- **Lifecycle API** (`register_lifecycle` / `activate_lifecycle` / `rotate_lifecycle` /
  `destroy_lifecycle` / `lifecycle_status`) must be used for trust-context setup.
  `softkey` returns `Unsupported` — that is correct and expected.

### Rules when integrating WSCD in clients

- **wallet-frontend**: use `WalletSigner` / `KeystoreSignerAdapter` — do not call
  `crypto.subtle` directly for wallet signing operations. The WSCD WASM module is
  loaded lazily; initialise it once per session. See `sirosfoundation/wallet-frontend`
  PR #164 and `docs/wsca-migration-spec` branch for the migration plan.
- **Kotlin SDK**: call UniFFI `WscdManager` from `sdk:keystore` module only.
  Do not duplicate key generation in `sdk:auth`.
- **Swift SDK**: same — `WscdManager` lives in `SirosKeystore`; do not call
  `SecKey` APIs for wallet keys.
- The **private data blob** (`WalletStateV4`) must NOT contain raw private key material.
  Key containers and credential state are stored in **two separate JWE containers**,
  both derived from the same PRF key but stored independently
  (`POST /private-data?type=keys` and `POST /private-data?type=state`).

---

## 17. Wallet Companion (Browser Extension)

When working in the `wallet-companion` repository:

- **Runtime**: WebExtensions API (Chrome, Firefox, Safari). Use `browser.*` via
  the `webextension-polyfill` shim — never `chrome.*` directly.
- **Language**: TypeScript; linting via **Biome** (`biome check --write`).
  Do not introduce ESLint or Prettier — Biome is the sole formatter/linter.
- **Build**: Vite for each browser target (`make build-chrome`, `make build-firefox`,
  `make build-safari`). Output lands in `dist/<browser>/`.
- **Package manager**: pnpm workspaces. Run `pnpm install` not `npm install`.
- **Testing**:
  - Unit / integration: **Vitest** (`vitest.config.ts` / `vitest.integration.config.ts`)
  - End-to-end: **Playwright** (`vitest.e2e.config.ts`) — requires a browser binary.
  - `make test` runs unit + integration; `make test-e2e` runs Playwright.
- **Architecture layers** (do not mix):
  - `src/background/` — service worker: storage, icon state, RPC dispatch
  - `src/content/dc-api/` — DC API intercept (`navigator.credentials.get` override)
  - `src/content/protocols/` — OID4VP protocol plugins (one file per variant)
  - `src/content/public-api/` — `window.WalletCompanion` auto-registration surface
  - `src/shared/` — types and utilities shared between layers
  - `packages/wcc-types/` — published TypeScript types for the public API
- **Protocol plugins**: each plugin implements the `ProtocolPlugin` interface.
  Add new protocol support as a new plugin file — do not modify existing plugins.
- **DC API routing**: `wallet-companion` delegates unrecognised protocols to the
  browser's native DC API implementation — never throw for unsupported protocols.
- **Security**: the extension content script runs in an isolated world. Never
  expose internal state to page scripts; use structured-clone-safe messages only.
- **Wallet registration** is ephemeral (session storage) unless the user opts in.
  Never persist wallet credentials or credential content in extension storage.

---

## 18. Wallet Messaging Protocol (WMP)

When working in `wmp`, `go-wmp`, or `wmp-js`:

- The canonical message schema is in `wmp/schema/` (JSON Schema Draft 2020-12).
  Always validate generated code/types against these schemas.
- Test vectors in `wmp/vectors/` are normative — implementations must pass them.
- Method naming: `wmp.<domain>.<action>` (e.g., `wmp.flow.start`).
- Capabilities are negotiated at session creation — only declared capabilities
  are valid for a given session.
- MLS encryption is optional; implementations must handle both `tls` and `mls`
  security modes gracefully.
- WMP is MCP-compatible (shares JSON-RPC 2.0 envelope) — keep this property
  when extending the protocol.

---

## 19. Version Control and PR Workflow

- **Never push directly to `main`** — always open a PR from a feature branch.
- Branch naming: `feat/<short-description>`, `fix/<short-description>`, `chore/<topic>`.
- PRs must not decrease test coverage.
- All PRs for `siros-conformance` changes must include updated conformance results.
- Do not merge PRs in the `webuild-consortium` GitHub org — comment/review only.
- Use SSH (`git+ssh://git@github.com/sirosfoundation/...`) for repos you push to.
