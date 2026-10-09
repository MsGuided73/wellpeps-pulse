"""Ingest: run collectors and store what they find.

For each collector: open a ``runs`` row (stage = collector name), make sure
its ``sources`` row exists, truncate oversized text (``bound_mention``),
upsert every mention (dedupe lives in the state
layer), and append a ``collected`` audit event for each mention that is new.
A collector that raises is recorded as a failed run; the others still run.
No model calls happen here.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from harvey.collectors.base import Collector, bound_mention
from harvey.models import AuditEvent, AuditEventType
from harvey.state import StateManager

logger = logging.getLogger("harvey.ingest")


@dataclass(frozen=True)
class CollectorResult:
    name: str
    run_id: str
    created: int = 0
    duplicates: int = 0
    errors: int = 0      # mentions that failed to store
    skipped: int = 0     # raw rows the collector rejected (bad url, bad JSON)
    error: str = ""      # set when the collector itself failed
    cost_usd: float = 0.0  # what a paid collector's run cost (Apify)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class IngestReport:
    results: list[CollectorResult] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(r.created for r in self.results)

    @property
    def duplicates(self) -> int:
        return sum(r.duplicates for r in self.results)

    @property
    def errors(self) -> int:
        return sum(r.errors for r in self.results)

    def lines(self) -> list[str]:
        """Human-readable summary, one line per collector."""
        out = []
        for r in self.results:
            line = (
                f"{r.name}: {r.created} created, {r.duplicates} duplicates, "
                f"{r.skipped} skipped, {r.errors} errors"
            )
            if r.cost_usd:
                line += f", cost ${r.cost_usd:.3f}"
            if r.error:
                line += f" — FAILED: {r.error}"
            out.append(line)
        return out


async def _run_one(
    state: StateManager, collector: Collector, since: datetime | None
) -> CollectorResult:
    name = collector.name
    source_id = await state.ensure_source(name)
    run_id = await state.start_run(stage=name, provider="collector")
    created = duplicates = errors = 0
    failure = ""

    try:
        async for mention in collector.collect(since):
            tagged = bound_mention(mention).model_copy(update={"source_id": source_id, "run_id": run_id})
            try:
                mention_id, is_new = await state.upsert_mention(tagged)
                if is_new:
                    await state.append_audit(AuditEvent(
                        mention_id=mention_id,
                        event=AuditEventType.COLLECTED,
                        actor=f"collector:{name}",
                        permalink=tagged.url,
                    ))
            except Exception as exc:
                errors += 1
                logger.error(f"{name}: failed to store {tagged.url}: {exc}")
                continue
            if is_new:
                created += 1
            else:
                duplicates += 1
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        logger.error(f"collector {name} failed: {failure}", exc_info=True)

    skipped = int(getattr(collector, "skipped", 0) or 0)
    # Paid collectors (Apify) report what the run cost; it feeds the monthly budget.
    cost = float(getattr(collector, "cost_usd", 0.0) or 0.0)
    if failure:
        await state.finish_run(run_id, status="failed", records=created, cost_usd=cost, error=failure)
    else:
        note = f"{errors} mention(s) failed to store" if errors else ""
        await state.finish_run(run_id, status="completed", records=created, cost_usd=cost, error=note)
    await state.touch_source(source_id)

    return CollectorResult(
        name=name, run_id=run_id, created=created, duplicates=duplicates,
        errors=errors, skipped=skipped, error=failure, cost_usd=cost,
    )


async def run_collectors(
    state: StateManager,
    collectors: list[Collector],
    since: datetime | None = None,
) -> IngestReport:
    """Run each collector once, isolated from the others' failures."""
    results = []
    for collector in collectors:
        try:
            results.append(await _run_one(state, collector, since))
        except Exception as exc:  # bookkeeping itself failed (DB down, ...)
            logger.error(f"ingest for {collector.name} failed: {exc}", exc_info=True)
            results.append(CollectorResult(name=collector.name, run_id="", error=str(exc)))
    return IngestReport(results=results)
