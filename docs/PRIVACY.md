# Privacy model

## Separate concepts, kept separate

ConsentBridge treats these as different inputs to a decision, not interchangeable "permission":

| Concept | In ConsentBridge | Example |
|---|---|---|
| **Identity** | Verified TideCloak token, linked to a user | "This is Jordan Walker" |
| **Organisational authority** | Role and organisation from the `users` table | "An insurance officer at Meridian" |
| **Authorisation reference / legal basis** | A claim, referral or reporting mandate the requester's organisation holds | Claim CLM-M-1001 lodged with Meridian |
| **Consent** | A recorded decision by the patient for a purpose and organisation, with optional field exclusions and expiry | Granted for insurance assessment, excluding procedure codes |
| **Organisational policy** | Purpose-specific field rules in the versioned policy | Insurers never receive clinician notes |
| **Additional approval** | A privacy reviewer's decision with justification | Medication released after review |

Consent is not treated as universally required. The initial policy uses three patterns:

- **Insurance claim assessment:** claim *and* current consent required.
- **Continuity of care:** the active referral is the basis; consent is not required, but a recorded refusal
  or withdrawal blocks sharing.
- **Public-health reporting:** an active mandate is the basis; consent does not apply because no
  patient-level data is released.

These patterns illustrate how different bases can be modelled. They are not legal advice and do not
establish that any real sharing would be lawful. Whether consent or another basis applies, and what it
permits, depends on the jurisdiction and must be decided by the deploying organisations.

## Data minimisation

- Clients can only name fields from a server-defined catalogue; anything else is rejected.
- Each purpose sorts fields into *allowed*, *review required* and *never shared*. Fields in none of those are
  "not needed for this purpose" and are denied.
- Requesting a whole-history bundle for a claim, or more than two never-shared fields, rejects the entire
  request rather than quietly trimming it.
- Only approved field identifiers are read from the database at release time; denied values are never
  loaded into the response.
- Sensitive notes are never freely allowed by any policy version (enforced by validation) and are hidden even
  from the custodian's own patient view.
- Administrators see request metadata with patient identity redacted.

## Aggregate statistics

Controls:

- Active mandate held by the agency and covering the metric.
- Only approved metrics and groupings; one grouping dimension per query; period up to the policy limit.
- No patient-level fields; no totals in responses.
- **Minimum group size** *k* (initially 5; `CB_AGGREGATE_MIN_GROUP_SIZE` can raise but never lower the
  policy value; production refuses values below 5). Cells with 0 < n < k are suppressed.
- **Complementary suppression:** if suppressed cells together total less than *k*, the next-smallest cells
  are also suppressed, so a single small cell cannot be derived by subtracting from a known total.
- Every query, its decision and the suppression counts are audited.

**Suppression reduces but does not eliminate re-identification risk.** Remaining risks include:

- *Differencing* across overlapping queries (different periods, groupings or populations).
- *Linkage* with external datasets (for example a known admission date and region).
- *Small populations* where even groups of five are identifying, or where all members share an attribute.
- *Repeated querying* over time.

For higher-risk data consider larger *k*, query budgets per agency, review of query patterns using the audit
trail, coarser groupings, or formal approaches such as differential privacy.

## Synthetic data

All seeded patients, organisations, diagnoses, notes and amounts are fictional. Names are invented, and
organisation names include "(synthetic)". The seed command refuses to run when `CB_ENVIRONMENT=production`.
