# ADR-002: End-user Authentication

**Status:** Approved 2026-07-13 with amendment: Clerk included in v0.6.
**Context:** How do humans (owner, operators, guests) sign in to a Platform's UI? The wizard should let each user pick their preferred auth mode per instance.

## Options analyzed

### Option A — Magic link + JWT propio (no passwords)

User enters email → Platform sends a one-time signed link → click sets session cookie.

**Advantages:**
- No passwords to hash, no reset flows, no "forgot password" support burden.
- Works everywhere email works.
- Nice fit for corporate use: only people with @company.com can be granted access.
- Cheap: any SMTP provider (Postmark, Resend, SES, self-hosted Postfix) works.

**Disadvantages:**
- Requires SMTP or a transactional email API. Ops friction: SPF/DKIM/DMARC setup.
- User experience has a delay (30-60s in bad networks).
- Deliverability: links can end in spam. Corporate mail filters may strip links.
- Not friendly for shared computers (email opens on phone, link needs to be on desktop).

**Trust model:** whoever controls the email controls the account. Standard for B2B SaaS.

---

### Option B — OAuth: Google + Microsoft + GitHub

User clicks "Sign in with Google" → OAuth dance → Platform gets email + name.

**Advantages:**
- Familiar UX: 3 clicks, no email waiting.
- Better security than passwords: MFA already enforced by the provider.
- Zero mail infrastructure.
- Natural for orgs on Google Workspace or M365.

**Disadvantages:**
- Each instance needs OAuth apps registered in each provider's console. This is real friction: create client ID, add redirect URL, verify domain, publish.
- Or: Console acts as a broker (a central OAuth app that any instance uses). Solves the friction but couples Platform to Console availability.
- Personal Google accounts, corporate accounts, and workspace accounts have different behaviors.
- Some corporates block social OAuth on desktop.

---

### Option C — Delegate to Console (Console = IdP)

Console has its own user directory; Platform verifies tokens signed by Console.

**Advantages:**
- One user directory, N Platforms. Users belong to a person (Rodrigo), not to each instance.
- Cross-instance access (giving a client user access to their own Client Space in your Platform) is trivial.
- SSO across all your Nexus instances.

**Disadvantages:**
- Console becomes critical for auth. Console downtime = users can't log in to Platforms.
- Requires Console to be reachable from every Platform (or offline verification via cached JWKs).
- Complex ownership model: is the user "mine" or Platform's?

**Trust model:** Console holds identity, Platform holds authorization.

---

### Option D — Clerk (or WorkOS / Auth0 / Supabase Auth) as managed provider

Third-party auth-as-a-service. User signs up in Clerk, Platform gets Clerk-issued token.

**Advantages:**
- Best UX out of the box: passkeys, social, MFA, magic link, all built in.
- Zero code for auth flows.
- Free tier is generous (10k MAUs on Clerk).

**Disadvantages:**
- External dependency. Nexus stops being self-contained.
- Vendor lock-in: user data on Clerk servers.
- Pricing cliff at scale (Clerk becomes expensive past ~10k MAUs).
- User has to create Clerk account and configure it — installation friction.
- Doesn't fit "sovereign personal OS" narrative for personal instances.

---

### Option E — Local password (bcrypt) + optional TOTP

Classic self-hosted app auth.

**Advantages:**
- No external dependency. Works fully offline (self-hosted personal).
- Simple mental model.
- TOTP (Google Authenticator) as 2FA is standard and battle-tested.

**Disadvantages:**
- Support burden: forgot password flow, reset emails.
- Weaker than magic link + MFA in practice (users pick bad passwords).
- No SSO story.

---

## Comparison table

| Dimension                 | A: Magic link | B: OAuth | C: Console-IdP | D: Clerk        | E: Password+TOTP |
|---------------------------|---------------|----------|----------------|-----------------|------------------|
| Install friction          | Low (SMTP)    | Medium   | Zero (auto)    | Medium (signup) | Zero             |
| Ops burden                | SMTP setup    | Register apps | Console up | External SLA    | Reset flows      |
| Depends on Internet       | Yes (SMTP)    | Yes      | Yes            | Yes             | **No**           |
| Corporate friendly        | Yes           | Excellent| Yes            | Yes             | Meh              |
| Passkeys / MFA            | Manual        | Provider | Manual         | Built-in        | TOTP manual      |
| Vendor lock              | No            | Provider | No             | Clerk           | No               |
| Cost                      | Free-cheap    | Free     | Free           | Free-$$$ at scale| Free            |

## Recommendation

**Offer multiple options in the wizard, backed by a common `auth/` module in Platform.** Each Persona gets a suitable default:

- **Personal** — default: **E (local password + TOTP)**. Offline-friendly, no SMTP required. Advanced users can switch to A, B, or D.
- **Family** — default: **A (magic link)**. Simple for non-technical family members.
- **Company** — default: **B (OAuth, prefer Google Workspace / M365)**. Alternatives: A, C, or D (Clerk for teams that want passkeys out of the box).
- **Community** — default: **A (magic link)** or **B**. D (Clerk) for public communities with heavy signup UX needs.
- **Client Space** — default: **C (Console-IdP)** to enable cross-instance sharing.
- **Custom** — user picks any of A/B/C/D/E.

**Included in v0.6:** Clerk (per amendment). Available as an opt-in provider in the wizard for users who prioritize UX polish (passkeys, social, MFA out of the box) over pure sovereignty. Wizard makes the trade-off explicit: enabling Clerk means auth data lives on Clerk servers; disabling Clerk keeps everything in-instance.

**Not v0.6:** WorkOS, Auth0, Supabase Auth. Reachable via the same auth-provider abstraction if demand appears — the interface below accepts any external IdP.

## Wizard impact

- Step 6 (Governance) grows a sub-section "Auth" with:
  - Provider: `password_totp | magic_link | oauth_google | oauth_microsoft | oauth_github | console_idp | clerk`
  - SMTP config (host, port, user, pass) if `magic_link`
  - OAuth client ID/secret if `oauth_*`
  - Clerk publishable key + secret key if `clerk`
- Credentials go through the credentials secure form, never plaintext in the manifest.
