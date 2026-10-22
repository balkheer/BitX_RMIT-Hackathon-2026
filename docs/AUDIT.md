# Audit chain

ConsentBridge records significant actions in `audit_events`, a hash-linked log. The mechanism is
**tamper-evident, not tamper-proof**: it reveals most changes after the fact; it cannot stop someone with
enough database privilege from making them.

## What is recorded

| Action | When |
|---|---|
| `AUTH_SESSION_STARTED` | First API call after 30 minutes of inactivity |
| `AUTH_DENIED` | Verified token whose subject isn't linked, or a disabled account (subject fingerprint only) |
| `REQUEST_SUBMITTED`, `POLICY_EVALUATED` | Every request creation, including rejected ones |
| `POLICY_PREVIEWED` | Policy checks run from the request form |
| `REVIEW_DECIDED` (+ `POLICY_EVALUATED`) | Reviewer decision; the justification is stored in `review_decisions` |
| `POLICY_REEVALUATED` | Release-time re-evaluation that changed or blocked the outcome |
| `DATA_RELEASED`, `RELEASE_BLOCKED` | Every release and every refused release |
| `ACCESS_DENIED` | Any 403 on a resource or endpoint |
| `PATIENT_RECORD_VIEWED` | Custodian staff opening a patient record |
| `POLICY_CHANGED`, `USER_CREATED`, `USER_UPDATED`, `ORGANIZATION_UPDATED`, `IDENTITY_LINKED` | Administration |
| `AUDIT_VERIFIED` | Each verification run and its result |
| `SYNTHETIC_DATA_SEEDED` | Marks events produced by the seed script |

Details hold identifiers, field names, codes and counts. They never hold patient values, tokens or
passwords: `append_event` rejects keys such as `token`, `password`, `secret`, `authorization`, `values` and
rejects floats, and tests scan the whole table for clinical text, aliases and JWTs.

## Hash inputs

```
event_hash = SHA-256( canonical_json({
  seq, occurred_at, actor_user_id, actor_display, actor_role, actor_org_id,
  action, outcome, resource_type, resource_id, request_id, related_org_ids,
  decision, policy_codes, details, prev_hash }) )
```

- `canonical_json`: UTF-8, keys sorted, separators `,` and `:` with no spaces, `NaN` disallowed.
- UUIDs as lowercase hyphenated strings; `related_org_ids` sorted; other lists keep order.
- `occurred_at` normalised to UTC with microseconds: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (Postgres `timestamptz`
  stores microseconds, so values round-trip exactly).
- The first event links to 64 zeros.

## Ordering and concurrency

Appends run inside the same transaction as the business change. Each append takes
`pg_advisory_xact_lock(<constant>)`, reads the head (`ORDER BY seq DESC LIMIT 1`), and inserts
`seq = head + 1`, `prev_hash = head.event_hash`. The lock is held until commit, so commit order equals chain
order and two writers can never extend the same head. `UNIQUE(seq)` backs this up. A rolled-back change
leaves no audit row. Tested with 8 threads × 15 appends: contiguous sequence, valid chain.

The lock serialises audit writes across the whole deployment. That is acceptable at this scale; at higher
volumes, partition the chain (for example per day with a daily anchor) or move to a dedicated log service.

## Verification

`POST /api/audit/verify` (admins and privacy reviewers), `python -m app.cli verify-audit`, and the
`audit-checkpoint` service hourly. The verifier streams events in `seq` order and reports the first failure:

| Check | Detects |
|---|---|
| `seq` starts at 1 and increases by one | Deleted events, gaps, duplicates |
| `prev_hash` equals the previous `event_hash` | Reordering, insertion, deletion, re-hashing of a single event |
| Recomputed hash equals stored `event_hash` | Modification of any hashed field |

The audit page also re-hashes each displayed row ("Matches"/"Mismatch") for a quick visual check; full
verification is the button.

Tests (`tests/test_audit.py`) tamper as a superuser inside a rolled-back transaction and confirm detection of
a modified decision, modified details, a single event edited and re-hashed, a deleted event, two swapped
events and a forged inserted event.

## Database protections

- The runtime role `cb_app` has `SELECT, INSERT` only on `audit_events` (and on `review_decisions`,
  `data_releases`), and no `DELETE` anywhere.
- Trigger `audit_events_no_update_delete` and `audit_events_no_truncate` reject those operations for every
  role, including the owner.
- A superuser can disable the trigger. That is why the chain and external checkpoints exist.

## Limits and stronger assurance

The chain alone cannot detect:

- **Truncation of the newest events.** The remaining chain is still valid.
- **Wholesale rewriting.** Someone who rewrites every event from some point and recomputes all hashes
  produces a valid chain.

Both are detectable by comparing against a head hash kept elsewhere. The `audit-checkpoint` service appends
`timestamp + {valid, checked, head_seq, head_hash}` hourly to `audit-checkpoints/heads.log`. For production:

1. Ship `heads.log` to write-once storage (S3 Object Lock, WORM) in a separate account.
2. Sign each checkpoint with a key that the database host cannot read, or publish it to a transparency log.
3. Restrict superuser access to break-glass procedures with their own logging.
4. Stream audit events to an independent append-only store as well as Postgres.
