# TideCloak integration

## Status

| Item | State |
|---|---|
| Frontend uses `@tidecloak/react` 0.14.33 (`TideCloakContextProvider`, `useTideCloak`, `/auth/redirect`, `silent-check-sso.html`) | Implemented per the package's own front-channel guide and the official React tutorial |
| Backend verifies TideCloak access tokens | Implemented in Python, mirroring the checks in Tide's `@tidecloak/verify` 0.14.33, plus stricter checks |
| Verification logic tested | Yes: 23 authentication tests with ES256 and EdDSA tokens signed by keys published through the adapter's `jwk` block |
| **Tested against a live TideCloak realm** | **No.** The build environment could not pull `tideorg/tidecloak-dev` (Docker Hub blocked) or reach a hosted realm. Real sign-in, token contents and the CSP origins required by the Tide enclave must be verified with the checklist below before any real use |

Sources used: the official [React tutorial](https://docs.tide.org/Languages/React/tidecloak-react-tutorial),
the [quickstart](https://docs.tide.org/Admin/Quick-deploy-test), and the published npm packages
`@tidecloak/react`, `@tidecloak/js` and `@tidecloak/verify` 0.14.33 (README, type definitions and the
verifier source). No endpoint, SDK method or configuration field was invented.

## How it fits together

- The browser loads the adapter from `/tidecloak.json` (`configUrl`). Tide documents this file as downloaded
  from tide-console: **Apps → your app → Credentials → Tide adapter → Download tidecloak.json**. It is public
  client configuration; still, keep it out of source control because it is deployment-specific.
- After sign-in TideCloak returns to `${origin}/auth/redirect`, which the app routes to `AuthRedirect`. If the
  URL carries `auth=failed`, the app records an expired session and calls `logout()`, as in Tide's tutorial.
- Every API call uses `getToken()` (refreshes when needed) and sends `Authorization: Bearer`.
- The API reads the same adapter (`CB_TIDECLOAK_CONFIG_PATH`) and verifies tokens:
  - issuer = `{auth-server-url}/realms/{realm}`;
  - keys from the adapter's `jwk` block when present, otherwise `{issuer}/protocol/openid-connect/certs`
    (cached for `CB_JWKS_CACHE_SECONDS`);
  - algorithms limited to `ES256, ES384, ES512, EdDSA` (Tide's verifier default; symmetric and `none` are
    refused by configuration validation);
  - `exp`/`nbf`/`iat` with 5 s tolerance; `exp`, `iat`, `iss`, `sub` required;
  - `azp` must equal the adapter's `resource` (client ID), as in Tide's verifier;
  - `typ` must be `Bearer`, so ID or refresh tokens can't be replayed as access tokens
    (disable with `CB_TIDECLOAK_REQUIRE_BEARER_TYP=false` only if your realm's tokens lack it);
  - optional `aud` check (`CB_TIDECLOAK_AUDIENCE`) and optional required realm/client role
    (`CB_TIDECLOAK_REQUIRED_ROLE`).
- Authorisation never comes from token roles. The verified issuer + `sub` is looked up in `users`; role and
  organisation come from that row.

## Setting up a realm (development)

1. Start TideCloak. Either `docker compose -f deploy/docker-compose.dev.yml up -d tidecloak` or the
   quickstart command:
   ```bash
   docker run --name mytidecloak -d -v .:/opt/keycloak/data/h2 -p 8080:8080 \
     -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=<choose one> \
     tideorg/tidecloak-dev:latest
   ```
2. Open `http://localhost:8080`, sign in as the bootstrap admin, complete **Set up this realm**, and link your
   Tide account when prompted.
3. In tide-console, **Apps → Add an app** for ConsentBridge. Set valid redirect URIs to
   `http://localhost:5173/*` and web origins to `http://localhost:5173` (production: your HTTPS domain).
   With quorum governance (QEA) enabled these edits are change requests that must be approved and committed
   before downloading the adapter.
4. Download `tidecloak.json` and save it as `frontend/public/tidecloak.json`; set
   `CB_TIDECLOAK_CONFIG_PATH` to the same file for the backend.
5. In **People → Add a user**, create users and send them their enrolment links.
6. Have each user sign in once. The "not provisioned" screen shows their identifier; link it:
   `python -m app.cli link-identity --username <seed user> --subject <identifier>`.
   The first administrator: `python -m app.cli create-admin --username … --display-name … --subject …`.

## Live verification checklist

Run these against the configured realm and record the results in [TEST_REPORT.md](TEST_REPORT.md):

1. `GET /readyz` reports `auth_configured: true` and the expected key source.
2. Signed out, `/requests` redirects to the sign-in screen; **Sign in with TideCloak** completes and returns
   via `/auth/redirect`.
3. An unlinked account sees "not provisioned"; `AUTH_DENIED` appears in the audit trail.
4. After linking, `/api/me` returns the role from ConsentBridge, regardless of realm roles.
5. Decode one access token and confirm `iss`, `azp`, `typ` and `sub` match what the verifier expects.
   If `typ` is absent, set `CB_TIDECLOAK_REQUIRE_BEARER_TYP=false` and note it.
6. Leave a session idle past the access-token lifetime: calls continue after silent refresh, or the app returns
   to sign-in with "Your session ended".
7. Sign out returns to the sign-in screen and old tokens stop working once expired.
8. In production, watch the browser console for CSP violations during sign-in and add the reported Tide
   origins to `CB_CSP_IDP_ORIGINS`.
9. Repeat workflows A–F from [DEMO.md](DEMO.md) with real accounts.
