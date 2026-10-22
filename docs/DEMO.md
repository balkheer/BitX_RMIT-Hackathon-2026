# Two-minute demonstration

Everything below uses the synthetic dataset and real application behaviour. Before presenting: link the
TideCloak accounts you'll use (see [TIDECLOAK.md](TIDECLOAK.md)), sign each into a separate browser profile,
and run through once. If the live realm has not been verified, say so rather than implying it has.

## Seeded accounts (ConsentBridge side, unlinked until you link them)

| Username | Person (fictional) | Role | Organisation |
|---|---|---|---|
| `j.walker` | Jordan Walker | Insurance officer | Meridian Health Insurance (synthetic) |
| `s.lim` | Sora Lim | Insurance officer | Kestrel Mutual Insurance (synthetic) |
| `h.nguyen` | Hana Nguyen | Hospital staff | St Aurelia General Hospital (synthetic) |
| `r.okafor` | Remi Okafor | Hospital staff | Harbourview Medical Centre (synthetic) |
| `m.chen` | Mei Chen | Privacy reviewer | St Aurelia General Hospital (synthetic) |
| `a.patel` | Arjun Patel | Privacy reviewer | Harbourview Medical Centre (synthetic) |
| `t.brennan` | Tess Brennan | Government health officer | Department of Public Health Analytics (synthetic) |
| (create with CLI) | | Platform administrator | ConsentBridge Platform Operations |

Useful references: claims `CLM-M-1001` (clean), `CLM-M-1002` (sensitive mental-health encounter),
`CLM-M-1003` (consent withdrawn), `CLM-M-1004` (consent excludes procedure code), `CLM-M-1005`; referrals
`REF-3001`, `REF-3003`, `REF-3004` (patient refused); mandates `MAN-PH-2026` (active), `MAN-PH-2024` (expired).

## Script

**0:00–0:15 · The problem.** "Hospitals, insurers and government all need health data, and today a login
often means broad access. ConsentBridge separates the two: TideCloak tells us who you are; ConsentBridge
decides, field by field, what you may see and why, and records it."

**0:15–0:35 · Valid claim (Jordan, insurer).** New request → Insurance claim assessment → claim
`CLM-M-1001` → tick Patient reference, Claim reference, Treatment date, Diagnosis code, Billing amount →
justification → **Check against policy** (ledger: all releasable) → **Submit request** → **Open released
data**. Point out: exactly those five fields; authorisation re-checked on open.

**0:35–0:55 · Excessive request (Jordan).** Same claim, add Full medical history and Sensitive notes →
**Check against policy**: rejected, `EXCESSIVE_SCOPE`. Then try Billing amount + Clinician notes: approved
with restrictions, clinician notes struck through as "Never shared for this purpose". Open the data: the
withheld field never reaches the browser.

**0:55–1:15 · Sensitive review (Jordan → Mei).** Claim `CLM-M-1002`, fields Diagnosis code, Medication,
Billing amount → Submit: **Pending review**, held until review. Switch to Mei (St Aurelia's reviewer) →
Review queue → open it → Approve with a justification. Switch back: approved; open data. Mention: Arjun at
Harbourview can't review it (not the record holder), Jordan can't review his own, and a reviewer can't
unlock clinician notes.

**1:15–1:35 · Aggregate statistics (Tess).** Statistics → mandate `MAN-PH-2026` → Number of encounters by
Diagnosis category, full synthetic range → Run. Mental health (3) and sexual health (2) are suppressed. Switch
grouping to Region: Inland (3) is suppressed and Coastal is complementary-suppressed so Inland can't be
derived. "Useful control, not a guarantee against re-identification."

**1:35–1:50 · Audit (Mei or admin).** Audit trail → **Verify integrity** → "Chain intact: all N events
verified" with head hash. Expand an event: identifiers and field names only, no values.

**1:50–2:00 · Close.** "Authentication answered *who*. Every disclosure you saw was decided by organisation,
role, purpose, claim or mandate, consent and review, then re-checked at release, and it's all on a
tamper-evident record."

## Backup moves if time allows

- As Jordan, paste a Harbourview patient URL (`/patients/<id>`): "You don't have access to this", and an
  `ACCESS_DENIED` event appears.
- As Hana, open request for `REF-3004`: rejected because the patient refused continuity sharing.
