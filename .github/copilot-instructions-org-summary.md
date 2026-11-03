SIROS digital-identity wallet — org-wide Copilot rules.
Full reference: https://github.com/sirosfoundation/.github/blob/main/.github/copilot-instructions.md

**CRYPTO** — Never implement crypto primitives. Use: go-cryptoutil (Go, brainpool/alg mapping), go-jose/go-jose/v4 + lestrrat-go/jwx/v3 (Go JWE/JWT), nimbus-jose-jwt (Kotlin), CryptoKit (Swift), WebCrypto/jose (TS). All wallet key operations via siros-wscd-manager WscdPlugin (keygen, sign, SecurityProperties). Algorithm mapping via go-cryptoutil.AlgorithmRegistry — never scatter algorithm constants.

**TRUST** — All trust decisions POST /evaluation to AuthZEN PDP (go-trust). Never evaluate X.509 chains locally. Frameworks: ETSI TSL/LoTE, OpenID Federation, DID Web, whitelist. Dev only: static:always-trusted.

**ACL** — go-spocp for rule-based access control via AuthZEN API. Rules in .spocp files, not inline strings.

**AUTH** — WebAuthn/FIDO2 passkeys only. No passwords, OTP, SMS. PRF extension → HKDF-SHA-256 (info="eDiplomas PRF") → AES-256-GCM main key → JWE credential container.

**TRANSPORT** — Transport-independent: native WebSocket | WMP WebSocket (wmp.v1) | HTTP+SSE. Go: implement wmp.Handler. SDKs: WmpWebSocketTransport / WmpHttpSseTransport share the same interface.

**PRIVACY** — Keys never leave client. Client-side credential matching. No credential content/nonces/PRF output in logs. Encrypted JWE storage (AES-256-GCM). Minimal JWT claims.

**EUDI** — OID4VCI (pre-auth, auth-code, deferred), OID4VP (direct_post, HAIP), SD-JWT VC, mdoc/ISO 18013-5, DCQL. ARF v2.8.0 normative. DPoP (RFC 9449) on all token requests; ephemeral key per session. Non-EUDI extensions via WMP profiles — do not patch OID4VC/VP handlers.

**CLIENTS (feature parity required)** — wallet-frontend (TS/React, reference impl, WSCD WASM), siros-sdk-kotlin (Android, UniFFI), siros-sdk-swift (iOS/macOS, UniFFI). New backend features need all three clients before merge. Cross-client format: privatedata-spec.

**PRIVATE DATA BLOB** — JWE A256GCMKW/A256GCM. Key chain: PRF → HKDF-SHA-256(info="eDiplomas PRF") → AES-GCM → ECDH private key → ECDH(mainKey.pub) → AES-KW → 256-bit main key → JWE plaintext. Schema v3 normative (v1/v2 read-only). Event-sourced WalletStateContainer. Optimistic concurrency: X-Private-Data-If-Match / ETag / 412→merge+retry. Conformance: privatedata-spec/conformance/.

**AUDIT** — All security events via go-siros-set emit.Emit(set.EventXxx, data). RFC 8417 SET with hash-chain + Merkle tree. Never plain log lines for audit events.

**TESTING** — ≥70% overall, ≥80% auth/crypto/storage/handlers, 96% go-spocp. Table-driven. In-memory storage for Go tests (t.Context()). siros-conformance must pass before merge.

**GO** — 1.21+. any not interface{}. errors.Is/As. Sentinel errors + fmt.Errorf("%w"). Store interface (memory→SQLite→MongoDB). ttlcache/v3 (configurable TTL). Config: YAML + env WALLET_<SECTION>_<KEY>. zap logging — no PII/keys/nonces. Structure: cmd/ internal/{api,domain,engine,service,storage}/ pkg/ docs/adr/.

**MULTI-TENANCY** — X-Tenant-ID header. JWT tenant_id claim is authoritative. Default tenant: "default" not "".

**SECURITY** — ORM/parameterised queries only. JWT user_id+tenant_id on every endpoint. SSRF: block RFC1918/loopback. Never ship always-trusted in production.

**INFRA** — Stateless. /healthz /readyz /metrics. ghcr.io/sirosfoundation/<repo>. docker compose (V2). Helm in helm-charts/.

**COMPLIANCE** — SID-<DOMAIN>-NN controls. grc derive && grc render after changes. go-grc MCP (grc serve --mcp): search_controls, compliance_gap_analysis, control_coverage.

**DEPRECATED** — Never use: wallet-backend-server (→ go-wallet-backend), go-wallet-as, wallet-e2e-tests, mtcvctm.

**PR WORKFLOW** — Never push to main. feat/<x> / fix/<x> / chore/<x> branches. PRs must not decrease coverage. Never merge PRs in webuild-consortium org (comment/review only).
