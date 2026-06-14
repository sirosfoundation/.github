## SIROS Foundation

SIROS Foundation builds open source infrastructure for digital identity. Our platform implements the EU Digital Identity Wallet ecosystem using open standards (OID4VCI, OID4VP, SD-JWT VC, mDL, OpenID Federation).

<picture>
  <img alt="SIROS ID Platform Architecture" src="https://developers.siros.org/img/architecture-overview.svg" width="100%">
</picture>

### Platform Components

| Component | Repository | Description |
|-----------|-----------|-------------|
| **Wallet Frontend** | [wallet-frontend](https://github.com/wwWallet/wallet-frontend) · [wallet-companion](https://github.com/sirosfoundation/wallet-companion) | React PWA (SPA) and browser extension — runs on user device |
| **Wallet Backend** | [go-wallet-backend](https://github.com/sirosfoundation/go-wallet-backend) | Go backend with WebAuthn, credential storage, OID4VCI/OID4VP |
| **Native SDKs** | [siros-sdk-kotlin](https://github.com/sirosfoundation/siros-sdk-kotlin) · [siros-sdk-swift](https://github.com/sirosfoundation/siros-sdk-swift) | Android and iOS SDKs for embedding wallet capabilities in native apps |
| **Issuer & Verifier** | [SUNET/vc](https://github.com/SUNET/vc) | Credential issuance (OID4VCI) and verification (OID4VP, OIDC, DC API) |
| **Trust Services** | [go-trust](https://github.com/sirosfoundation/go-trust) | AuthZEN PDP — ETSI TSL, LoTE, OpenID Federation, DID:web/webvh |
| **Trust Ecosystem** | [g119612](https://github.com/sirosfoundation/g119612) · [goFF](https://github.com/sirosfoundation/goFF) · [go-spocp](https://github.com/sirosfoundation/go-spocp) | Trust list tooling, OpenID Federation, access control |
| **Credential Registry** | [registry.siros.org](https://github.com/sirosfoundation/registry.siros.org) · [registry-cli](https://github.com/sirosfoundation/registry-cli) | VCTM metadata registry and TS11-compliant catalogue builder |
| **WSCD Manager** | [siros-wscd-manager](https://github.com/sirosfoundation/siros-wscd-manager) | Hardware key management abstraction (software, R2PS, FIDO2) |

### Getting Started

- **Documentation** — [developers.siros.org](https://developers.siros.org)
- **Try it** — [id.siros.org](https://id.siros.org)
- **Local dev** — [sirosid-dev](https://github.com/sirosfoundation/sirosid-dev) (`make up`)
- **About us** — [siros.org](https://siros.org)

