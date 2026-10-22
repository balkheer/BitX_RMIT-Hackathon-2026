# ConsentBridge — Summary

**Identity is necessary, but never sufficient.**

RMIT Hackathon 2026 — Challenge 2: Build Without Blind Trust

## The problem

Hospitals, insurers and government health agencies have to share patient information. In many systems, a successful login is treated as permission: once someone is authenticated, they can see far more than their task requires.

An insurer assessing a claim for a broken ankle should not be able to read a patient's mental-health notes. Yet when access is decided by **who you are** rather than **what you need and why**, that is exactly what can happen. Over-sharing harms patients, exposes organisations to risk, and leaves no clear record of who saw what.

## Our solution

ConsentBridge is a web platform that separates authentication from authorisation. TideCloak proves who a user is. ConsentBridge then decides, field by field, what that user may see, and records every decision.

Every data-sharing request is evaluated on the server against:

- the requester's role and organisation, taken from ConsentBridge records rather than from anything the user sends;
- the declared purpose: insurance claim assessment, continuity of care, or public-health reporting;
- an authorisation basis held by the requester's organisation: an insurance claim, a referral, or a reporting mandate;
- the patient's recorded consent, where the purpose requires it;
- whether a human privacy reviewer must approve it first.

The outcome is one of **approved**, **approved with restrictions**, **pending review**, or **rejected**, with a plain-language explanation of every withheld field.

## Value

- **Minimum necessary disclosure.** Requesters receive only the fields their purpose justifies. Withheld values never reach the browser.
- **Human oversight where it matters.** Sensitive requests go to a privacy reviewer at the hospital that holds the record. Reviewers must justify decisions, cannot review their own organisation's requests, and cannot override hard limits.
- **Decisions stay current.** Authorisation is re-checked every time data is opened, so a withdrawn consent or closed referral takes effect immediately.
- **Safer statistics.** Government agencies receive aggregate counts only, with small groups automatically suppressed to reduce re-identification risk.
- **Accountability.** Every request, decision, review, release and denied attempt is written to a hash-linked, tamper-evident audit trail that can be verified with one click.
- **Built to be trusted.** 135 backend and 11 frontend automated tests pass, and all six required workflows were verified end to end against the running system.

## The role of TideCloak

TideCloak is ConsentBridge's identity layer: it answers the question "who is this?". Users sign in through TideCloak with the official TideCloak React SDK, and TideCloak issues the access token used for every API call.

ConsentBridge never trusts that token blindly. The backend independently verifies its signature against the realm's keys, the permitted algorithms, the issuer, expiry and intended client, mirroring the checks in Tide's own verification library. A verified identity is then mapped to a ConsentBridge user record that an administrator has approved. Only that record supplies the user's role and organisation.

In short: **TideCloak establishes identity; ConsentBridge decides access.** A valid TideCloak login is necessary to use the system, but it is never sufficient to see any patient data.

## Mocked or simulated functionality

To be clear about what is real and what is not:

- **All data is synthetic.** Every patient, organisation, diagnosis, note and amount is fictional. No real patient data is used.
- **Live TideCloak sign-in was not completed.** During realm setup, the TideCloak server reported "The vendor key was created but the license could not be set up", so an end-to-end sign-in against a live TideCloak realm could not be verified. The TideCloak integration code is complete, and token verification was tested with locally generated signing keys standing in for the realm's keys.
- **Demo sign-in in the video.** Any sign-in in the demonstration video that shows a "Demo mode" banner is simulated: a seeded user was selected instead of signing in through TideCloak. Everything after sign-in is real system behaviour: role checks, policy decisions, review, release and audit.
- **Local deployment only.** The prototype runs locally with Docker. Production deployment configuration is included but has not been deployed to a public host.
- **Example policy rules.** Consent and legal-basis rules model plausible organisational policy for demonstration. They are not legal advice and do not by themselves make any data sharing lawful.

## At a glance

| Component | Status |
|---|---|
| Policy engine and APIs | Implemented and tested |
| Web application | All required screens implemented |
| Workflows A–F | Verified end to end |
| Audit integrity | Tampering detected in tests |
| TideCloak integration | Implemented; live sign-in not yet verified |
| Data | Synthetic only |

## Submission contents

- `sourcecodes.zip` — application source code, with full documentation in `README.md` and the `docs/` folder inside it
- `summary.md` — this document
- `video.mp4` — two-minute working demonstration
- `RUN_INSTRUCTIONS.md` — how to launch and test the prototype



Set-Content RUN_INSTRUCTIONS.md -Encoding utf8 -Value @'
# ConsentBridge — Run Instructions

How to launch and test the prototype. All data is synthetic.

## Requirements

- **Docker Desktop** (Windows, macOS or Linux). Nothing else is needed to run the app.
- An internet connection that does **not** intercept HTTPS. Some university or corporate networks do; if the build fails with certificate errors (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`), switch to another network such as a phone hotspot for the build.

## 0. Get the code

Download `sourcecodes.zip` from this repository, extract it, and open a terminal inside the extracted `consentbridge` folder (the one containing `backend`, `frontend` and `deploy`). All commands below run from there.

## 1. Launch

    docker compose -f deploy/docker-compose.local.yml up -d --build

This builds and starts PostgreSQL, TideCloak, the API and the web app, runs the database migrations, and loads the synthetic dataset. The first build takes a few minutes.

Check that everything is running:

    docker compose -f deploy/docker-compose.local.yml ps -a

You should see `db`, `tidecloak`, `api` and `web` running. `setup` runs once and exits with code 0.

**If `tidecloak` shows `Exited (1)`** (its data folder isn't writable on first start), fix it and start it again:

    docker run --rm -v consentbridge-local_tidecloak-local:/data postgres:16-alpine chmod 0777 /data
    docker compose -f deploy/docker-compose.local.yml up -d tidecloak

## 2. Configure TideCloak

1. Open `http://localhost:8080` (or `http://127.0.0.1:8080`) and sign in with `admin` / `change-me-local-only`.
2. Complete **Set up this realm**: project name, contact email, accept the terms, **Create & set up realm**.
3. Link your Tide account using the enrolment link shown.
4. In tide-console, go to **Apps → Add an app** and set the valid redirect URI to `http://localhost:5173/*` and the web origin to `http://localhost:5173`.
5. Open the app and go to **Credentials → Tide adapter → Download tidecloak.json**.
6. Save the file as `deploy/local-config/tidecloak.json`, then restart the API:

    docker compose -f deploy/docker-compose.local.yml restart api

> **Known issue:** in our environment, realm setup stopped with "The vendor key was created but the license could not be set up". This comes from Tide's licensing service. If you see it, live TideCloak sign-in can't be completed until it is resolved on Tide's side. See `summary.md` for what this affects.

## 3. Open the app and sign in

1. Open `http://localhost:5173` and choose **Sign in with TideCloak**.
2. The first sign-in shows **"not provisioned"** with your identifier. This is expected: a valid login alone grants nothing. Link the identifier to a seeded user:

    docker compose -f deploy/docker-compose.local.yml exec api python -m app.cli link-identity --username j.walker --subject <identifier>

3. Refresh the page. To try other roles, create more TideCloak users and link each to a seeded user:

| Username | Role | Organisation (synthetic) |
|---|---|---|
| `j.walker` | Insurance officer | Meridian Health Insurance |
| `s.lim` | Insurance officer | Kestrel Mutual Insurance |
| `h.nguyen` | Hospital staff | St Aurelia General Hospital |
| `r.okafor` | Hospital staff | Harbourview Medical Centre |
| `m.chen` | Privacy reviewer | St Aurelia General Hospital |
| `a.patel` | Privacy reviewer | Harbourview Medical Centre |
| `t.brennan` | Government health officer | Department of Public Health Analytics |

For a platform administrator:

    docker compose -f deploy/docker-compose.local.yml exec api python -m app.cli create-admin --username ops.admin --display-name "Ops Admin" --subject <identifier>

## 4. Test the prototype

Each scenario below matches a required workflow.

**A. Valid insurance claim** (as `j.walker`): New request → Insurance claim assessment → claim `CLM-M-1001` → tick Patient reference, Claim reference, Treatment date, Diagnosis code, Billing amount → **Check against policy** → **Submit request** → **Open released data**. Only those five fields are returned.

**B. Excessive request** (as `j.walker`): same claim, but tick Full medical history and Sensitive notes → the request is **rejected**. Try Billing amount + Clinician notes instead → **approved with restrictions**; clinician notes are withheld and never sent.

**C. Government statistics** (as `t.brennan`): Statistics → mandate `MAN-PH-2026` → Number of encounters by Diagnosis category → **Run query**. Mental health and sexual health are **suppressed** (fewer than 5). Group by Region: Inland is suppressed, and Coastal is also hidden so Inland can't be worked out.

**D. Unauthorised access** (as `j.walker`): open a patient record URL copied while signed in as `h.nguyen` (`/patients/<id>`). You get **"You don't have access"** (HTTP 403), and an `ACCESS_DENIED` event appears in the audit trail.

**E. Sensitive review** (as `j.walker`, then `m.chen`): claim `CLM-M-1002` (a mental-health encounter), fields Diagnosis code, Medication, Billing amount → **Pending review**; nothing is released yet. Sign in as `m.chen` → **Review queue** → open it → approve with a justification. Back as `j.walker`, the reviewed fields can now be opened. `a.patel` (another hospital) cannot review it.

**F. Audit integrity** (as `m.chen` or the administrator): **Audit trail** → **Verify integrity** → "Chain intact: all N events verified", with the head hash.

### Automated tests (optional; needs Python 3.12+, Node 22 and PostgreSQL)

Backend (135 tests; the test database is dropped and recreated on each run), in PowerShell:

    cd backend
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements-dev.txt
    $env:CB_TEST_DATABASE_URL="postgresql+psycopg://cb_app:<pw>@127.0.0.1:5432/consentbridge_test"
    $env:CB_TEST_MIGRATION_DATABASE_URL="postgresql+psycopg://cb_owner:<pw>@127.0.0.1:5432/consentbridge_test"
    $env:CB_TEST_SUPERUSER_URL="postgresql+psycopg://postgres:<pw>@127.0.0.1:5432/postgres"
    pytest -q

Frontend (11 tests, lint, type check and production build):

    cd frontend
    npm install
    npm run lint
    npm run typecheck
    npm test
    npm run build

Recorded results are in `docs/TEST_REPORT.md` inside `sourcecodes.zip`.

## 5. Stop or reset

    docker compose -f deploy/docker-compose.local.yml down
    docker compose -f deploy/docker-compose.local.yml down -v

The first keeps your data; the second deletes it.

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails with certificate errors | Your network intercepts HTTPS. Use another network for the build. |
| `http://localhost:8080` doesn't load | Wait a minute, try `http://127.0.0.1:8080`, and check `tidecloak` isn't `Exited` (see step 1). |
| Sign-in screen says sign-in is unavailable | `deploy/local-config/tidecloak.json` is missing; add it and restart `api`. |
| "Identity verified but not provisioned" | Expected on first sign-in. Link the identifier (step 3). |
| Everything returns 401 | The adapter file doesn't match the realm or client; download it again and restart `api`. |

More detail: `README.md` and the `docs/` folder inside `sourcecodes.zip` (architecture, API, threat model, audit design, test report).
