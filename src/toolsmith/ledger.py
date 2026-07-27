"""The cost ledger: an append-only record of every token this project ever spent.

Two jobs.

1. **Accounting.** Every provider call writes one row. ``costs.csv`` is the
   artifact the README points at when it claims a total, and it is regenerated,
   never hand-edited.
2. **Enforcement.** The ledger is also the circuit breaker. A run that would
   push cumulative spend past ``configs/budget.yaml`` raises
   :class:`BudgetExceeded` *before* the request goes out, not after the invoice
   arrives. This is the mechanism that makes "under $20" a guarantee rather
   than a hope.

Rows carry ``provenance`` so that simulated and live spend can never be
confused with each other in a published table.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from toolsmith.config import REPO_ROOT, BudgetPolicy

Provenance = Literal["simulated", "live", "replayed", "cached"]

LEDGER_PATH = Path(os.environ.get("TOOLSMITH_LEDGER", REPO_ROOT / "eval" / "costs.csv"))

_FIELDS = [
    "ts",
    "run_id",
    "task_id",
    "pipeline",
    "role",
    "model_key",
    "provider",
    "provenance",
    "tokens_in",
    "tokens_cached_in",
    "tokens_out",
    "usd",
    "latency_s",
    "ok",
]


class BudgetExceeded(RuntimeError):  # noqa: N818 - reads as a domain event, not a stack trace
    """Raised before a call that would breach the configured spend cap."""


@dataclass(slots=True)
class LedgerEntry:
    run_id: str
    task_id: str
    pipeline: str
    role: str
    model_key: str
    provider: str
    provenance: Provenance
    tokens_in: int
    tokens_cached_in: int
    tokens_out: int
    usd: float
    latency_s: float
    ok: bool = True
    ts: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))


class CostLedger:
    """Thread-safe append-only ledger with a hard budget gate.

    The ledger only *enforces* against live spend. Simulated rows are recorded
    for completeness (so the report can state "this run would have cost $X live")
    but never consume the real budget, because they never leave the process.
    """

    def __init__(
        self,
        path: Path | None = None,
        policy: BudgetPolicy | None = None,
        run_id: str = "adhoc",
    ) -> None:
        self.path = path or LEDGER_PATH
        self.policy = policy or BudgetPolicy()
        self.run_id = run_id
        self._lock = threading.Lock()
        self._entries: list[LedgerEntry] = []
        self._run_live_usd = 0.0
        self._prior_live_usd = self._read_prior_live_total()

    # ------------------------------------------------------------- reading --
    def _read_prior_live_total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        with self.path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("provenance") == "live":
                    total += float(row.get("usd") or 0.0)
        return total

    @property
    def live_total_usd(self) -> float:
        """Cumulative real money spent, across every run ever recorded."""
        return self._prior_live_usd + self._run_live_usd

    @property
    def run_total_usd(self) -> float:
        return sum(e.usd for e in self._entries)

    @property
    def simulated_total_usd(self) -> float:
        return sum(e.usd for e in self._entries if e.provenance == "simulated")

    def remaining_usd(self) -> float:
        return max(0.0, self.policy.cap_usd - self.live_total_usd)

    # ------------------------------------------------------------ enforcing --
    def check_affordable(self, projected_usd: float, provenance: Provenance) -> None:
        """Raise if this call would breach the cap. Called *before* the request."""
        if provenance != "live" or not self.policy.stop_on_breach:
            return
        if self.live_total_usd + projected_usd > self.policy.cap_usd:
            raise BudgetExceeded(
                f"call would take live spend to "
                f"${self.live_total_usd + projected_usd:.4f}, over the "
                f"${self.policy.cap_usd:.2f} cap in configs/budget.yaml. "
                f"Raise the cap deliberately or run with --provider simulated."
            )
        if self._run_live_usd + projected_usd > self.policy.per_run_cap_usd:
            raise BudgetExceeded(
                f"this run would spend ${self._run_live_usd + projected_usd:.4f}, "
                f"over the ${self.policy.per_run_cap_usd:.2f} per-run cap."
            )

    # ------------------------------------------------------------- writing --
    def record(self, entry: LedgerEntry) -> LedgerEntry:
        with self._lock:
            self._entries.append(entry)
            if entry.provenance == "live":
                self._run_live_usd += entry.usd
        return entry

    def flush(self) -> Path:
        """Append this run's entries to ``costs.csv``, creating it if needed."""
        with self._lock:
            entries = list(self._entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists()
        with self.path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDS)
            if not exists:
                writer.writeheader()
            for entry in entries:
                writer.writerow({k: asdict(entry)[k] for k in _FIELDS})
        return self.path

    # ------------------------------------------------------------ reporting --
    def summary(self) -> dict[str, float | int]:
        by_provenance: dict[str, float] = {}
        for entry in self._entries:
            by_provenance[entry.provenance] = by_provenance.get(entry.provenance, 0.0) + entry.usd
        return {
            "calls": len(self._entries),
            "tokens_in": sum(e.tokens_in for e in self._entries),
            "tokens_cached_in": sum(e.tokens_cached_in for e in self._entries),
            "tokens_out": sum(e.tokens_out for e in self._entries),
            "usd_run": round(self.run_total_usd, 6),
            "usd_live_cumulative": round(self.live_total_usd, 6),
            "usd_remaining": round(self.remaining_usd(), 6),
            **{f"usd_{k}": round(v, 6) for k, v in by_provenance.items()},
        }

    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)


@dataclass(slots=True)
class BudgetAudit:
    """The answer to "have we stayed under $20", with its working shown."""

    path: str
    rows: int
    by_provenance: dict[str, float]
    live_usd: float
    cap_usd: float
    remaining_usd: float

    @property
    def within_cap(self) -> bool:
        return self.live_usd <= self.cap_usd


def audit_csv(path: Path | None = None, policy: BudgetPolicy | None = None) -> BudgetAudit:
    """Read ``costs.csv`` and report totals. Backs ``toolsmith ci budget``."""
    path = path or LEDGER_PATH
    policy = policy or BudgetPolicy()
    totals: dict[str, float] = {}
    rows = 0
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                rows += 1
                prov = row.get("provenance", "unknown")
                totals[prov] = totals.get(prov, 0.0) + float(row.get("usd") or 0.0)
    live = totals.get("live", 0.0)
    return BudgetAudit(
        path=str(path),
        rows=rows,
        by_provenance={k: round(v, 6) for k, v in sorted(totals.items())},
        live_usd=round(live, 6),
        cap_usd=policy.cap_usd,
        remaining_usd=round(max(0.0, policy.cap_usd - live), 6),
    )
