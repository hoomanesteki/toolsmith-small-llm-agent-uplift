"""The budget cap is only a guarantee if it is enforced before the request."""

from __future__ import annotations

import csv

import pytest

from toolsmith.config import BudgetPolicy
from toolsmith.ledger import BudgetExceeded, CostLedger, LedgerEntry, audit_csv


def entry(usd: float, provenance: str = "live", **kw) -> LedgerEntry:
    defaults: dict = {
        "run_id": "r1",
        "task_id": "t1",
        "pipeline": "p",
        "role": "executor",
        "model_key": "m",
        "provider": "groq",
        "tokens_in": 100,
        "tokens_cached_in": 0,
        "tokens_out": 50,
        "latency_s": 0.1,
    }
    defaults.update(kw)
    return LedgerEntry(provenance=provenance, usd=usd, **defaults)  # type: ignore[arg-type]


@pytest.fixture
def ledger(tmp_path):
    return CostLedger(
        path=tmp_path / "costs.csv",
        policy=BudgetPolicy(cap_usd=1.0, warn_at_usd=0.5, per_run_cap_usd=0.4),
        run_id="test",
    )


def test_simulated_spend_never_touches_the_cap(ledger):
    for _ in range(1000):
        ledger.check_affordable(5.0, "simulated")
        ledger.record(entry(5.0, "simulated"))
    assert ledger.live_total_usd == 0.0
    assert ledger.remaining_usd() == 1.0
    assert ledger.simulated_total_usd == 5000.0


def test_live_spend_is_blocked_at_the_per_run_cap(ledger):
    ledger.check_affordable(0.3, "live")
    ledger.record(entry(0.3))
    with pytest.raises(BudgetExceeded, match="per-run cap"):
        ledger.check_affordable(0.2, "live")


def test_live_spend_is_blocked_at_the_global_cap(tmp_path):
    ledger = CostLedger(
        path=tmp_path / "costs.csv",
        policy=BudgetPolicy(cap_usd=1.0, warn_at_usd=0.5, per_run_cap_usd=1.0),
    )
    ledger.check_affordable(0.9, "live")
    ledger.record(entry(0.9))
    with pytest.raises(BudgetExceeded, match=r"cap in configs/budget\.yaml"):
        ledger.check_affordable(0.2, "live")


def test_prior_spend_is_read_back_from_disk(tmp_path):
    path = tmp_path / "costs.csv"
    first = CostLedger(
        path=path, policy=BudgetPolicy(cap_usd=1.0, warn_at_usd=0.5, per_run_cap_usd=1.0)
    )
    first.record(entry(0.7))
    first.flush()

    second = CostLedger(
        path=path, policy=BudgetPolicy(cap_usd=1.0, warn_at_usd=0.5, per_run_cap_usd=1.0)
    )
    assert second.live_total_usd == pytest.approx(0.7)
    with pytest.raises(BudgetExceeded):
        second.check_affordable(0.5, "live")


def test_flush_appends_and_keeps_one_header(tmp_path):
    path = tmp_path / "costs.csv"
    for _ in range(3):
        led = CostLedger(path=path, policy=BudgetPolicy(cap_usd=100.0, warn_at_usd=1.0))
        led.record(entry(0.01))
        led.flush()
    text = path.read_text()
    assert text.count("provenance") == 1
    with path.open() as fh:
        assert len(list(csv.DictReader(fh))) == 3


def test_only_live_rows_are_persisted(tmp_path):
    """The ledger accounts for real money, and only real money.

    A single matrix run produces about a quarter of a million simulated rows.
    Persisting them made costs.csv 46 MB of noise around a $0.00 signal, which
    is the opposite of what a file called "costs" is for. The simulated totals
    are not lost: they go in the run manifest, so the report can still say what
    the same evidence would have cost to buy.
    """
    path = tmp_path / "costs.csv"
    led = CostLedger(path=path, policy=BudgetPolicy(cap_usd=100.0, warn_at_usd=1.0))
    led.record(entry(0.10, "live"))
    led.record(entry(2.00, "simulated"))
    led.flush()

    audit = audit_csv(path, BudgetPolicy(cap_usd=1.0, warn_at_usd=0.5, per_run_cap_usd=1.0))
    assert audit.by_provenance == {"live": 0.1}
    assert audit.live_usd == pytest.approx(0.10)
    assert audit.within_cap is True

    # The in-memory summary still reports both, because a run needs to know
    # what it would have cost.
    assert led.summary()["usd_simulated"] == pytest.approx(2.0)


def test_a_purely_simulated_run_writes_no_ledger_file(tmp_path):
    path = tmp_path / "costs.csv"
    led = CostLedger(path=path, policy=BudgetPolicy(cap_usd=100.0, warn_at_usd=1.0))
    for _ in range(50):
        led.record(entry(1.0, "simulated"))
    led.flush()
    assert not path.exists(), "nothing was spent, so there is nothing to account for"


def test_audit_flags_a_breach(tmp_path):
    path = tmp_path / "costs.csv"
    led = CostLedger(path=path, policy=BudgetPolicy(cap_usd=100.0, warn_at_usd=1.0))
    led.record(entry(25.0, "live"))
    led.flush()
    assert audit_csv(path, BudgetPolicy(cap_usd=20.0, warn_at_usd=5.0)).within_cap is False


def test_summary_shape(ledger):
    ledger.record(entry(0.01, "live", tokens_in=1000, tokens_cached_in=800, tokens_out=200))
    summary = ledger.summary()
    assert summary["calls"] == 1
    assert summary["tokens_cached_in"] == 800
    assert summary["usd_remaining"] == pytest.approx(0.99)
