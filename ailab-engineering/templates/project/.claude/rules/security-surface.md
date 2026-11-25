---
paths:
  - "**/auth/**"
  - "**/api/public/**"
  - "**/tenancy/**"
  - "**/billing/**"
  - "**/webhooks/**"
  - "**/fetch_security*.py"
  - "**/*session*.py"
---

# Security-sensitive code (T3 by default)

- Identity and workspace come from the server-side session, never from request
  parameters. Every new query is scoped by it.
- Operator/admin routes never appear in the public application profile.
- State-changing routes: CSRF + Origin checks; strict Host allowlist.
- User-supplied URLs: resolve, block private/loopback/link-local/metadata
  (IPv4 + IPv6), re-validate after every redirect, cap size and time.
- `UNKNOWN != NO`: a fetch or parse failure is never reported as "link lost" or
  "noindex"; it is an inconclusive result.
- Webhooks: verify signatures, make handlers idempotent, enforce entitlements on
  the backend.
- No secrets in code, tests, fixtures, logs or error responses.
- Run the `security-reviewer` subagent before asking for owner acceptance.
