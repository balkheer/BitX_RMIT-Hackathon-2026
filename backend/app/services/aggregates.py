"""Aggregate statistics with small-group suppression.

Patient-level rows are read inside the server only; the response contains
group labels and counts. Cells with 0 < n < k are suppressed (primary). If
anything is suppressed and the suppressed cells together total less than k,
the next-smallest cells are also suppressed (secondary) so the suppressed
remainder cannot be recovered from a known total.

This lowers re-identification risk; it does not eliminate it. Differencing
across overlapping queries, external linkage and very small populations remain
risks. See docs/PRIVACY.md.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Encounter, Patient
from app.policy.catalog import AGGREGATE_GROUPINGS, AGGREGATE_METRICS
from app.policy.engine import AggregateQuery
from app.schemas import AggregateGroupOut, AggregateResultOut


def age_band(birth_year: int, on: dt.date) -> str:
    age = on.year - birth_year
    if age >= 80:
        return "80+"
    lo = (max(age, 0) // 10) * 10
    return f"{lo}-{lo + 9}"


def compute(db: Session, q: AggregateQuery, k: int, secondary: bool = True) -> AggregateResultOut:
    rows = db.execute(
        select(
            Encounter.id,
            Encounter.patient_id,
            Encounter.treatment_date,
            Encounter.diagnosis_category,
            Patient.region,
            Patient.sex,
            Patient.birth_year,
        )
        .join(Patient, Patient.id == Encounter.patient_id)
        .where(Encounter.treatment_date >= q.period_start, Encounter.treatment_date <= q.period_end)
    ).all()

    buckets: dict[tuple[str, ...], set[object]] = defaultdict(set)
    for r in rows:
        values = {
            "diagnosis_category": r.diagnosis_category,
            "region": r.region,
            "sex": r.sex,
            "age_band": age_band(r.birth_year, r.treatment_date),
        }
        key = tuple(values[g] for g in q.group_by)
        buckets[key].add(r.id if q.metric == "encounter_count" else r.patient_id)

    counts = {key: len(v) for key, v in buckets.items()}
    suppressed: set[tuple[str, ...]] = {key for key, n in counts.items() if 0 < n < k}
    secondary_count = 0
    if secondary and suppressed:
        remaining = sorted((n, key) for key, n in counts.items() if key not in suppressed)
        while sum(counts[s] for s in suppressed) < k and remaining:
            _, key = remaining.pop(0)
            suppressed.add(key)
            secondary_count += 1

    groups: list[AggregateGroupOut] = []
    for key in sorted(counts):
        is_sup = key in suppressed
        reason = None
        if is_sup:
            reason = f"Fewer than {k}" if counts[key] < k else "Complementary suppression"
        groups.append(
            AggregateGroupOut(
                group=dict(zip(q.group_by, key, strict=True)),
                value=None if is_sup else counts[key],
                suppressed=is_sup,
                suppression_reason=reason,
            )
        )
    return AggregateResultOut(
        metric=q.metric,
        metric_label=AGGREGATE_METRICS[q.metric],
        group_by=list(q.group_by),
        period_start=q.period_start,
        period_end=q.period_end,
        min_group_size=k,
        groups=groups,
        suppressed_groups=len(suppressed),
        secondary_suppressions=secondary_count,
        notice=(
            f"Groups with fewer than {k} are suppressed, with complementary suppression where needed. "
            "Suppression reduces but does not eliminate re-identification risk. "
            f"Grouping: {', '.join(AGGREGATE_GROUPINGS[g] for g in q.group_by)}."
        ),
    )
