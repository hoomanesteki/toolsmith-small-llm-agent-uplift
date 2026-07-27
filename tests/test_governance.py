"""Governance gates. Each of these is a README claim with an exit code."""

from __future__ import annotations

import json

from toolsmith.config import load_registry
from toolsmith.governance import (
    LineageLog,
    Provenance,
    allowed_generators,
    content_hash,
    oracle_provenance,
    scan,
)
from toolsmith.governance.agnostic import scan_source


def _write_rows(tmp_path, rows: list[dict]) -> None:
    target = tmp_path / "data" / "train"
    target.mkdir(parents=True)
    (target / "sft.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_firewall_blocks_a_forbidden_generator(tmp_path):
    _write_rows(
        tmp_path,
        [
            {
                "text": "distilled trajectory",
                "provenance": Provenance(
                    source_type="model_output", generator_model="claude-opus-5"
                ).model_dump(mode="json"),
            }
        ],
    )
    report = scan(root=tmp_path, registry=load_registry())
    assert not report.clean
    assert report.violations[0].generator_model == "claude-opus-5"
    assert "forbidden" in report.violations[0].reason


def test_firewall_allows_a_training_legal_generator(tmp_path):
    _write_rows(
        tmp_path,
        [
            {
                "text": "trajectory",
                "provenance": Provenance(
                    source_type="model_output", generator_model="groq-oss-120b"
                ).model_dump(mode="json"),
            }
        ],
    )
    assert scan(root=tmp_path, registry=load_registry()).clean


def test_firewall_allows_oracle_rows(tmp_path):
    _write_rows(
        tmp_path,
        [{"text": "gold", "provenance": oracle_provenance(seed=7).model_dump(mode="json")}],
    )
    report = scan(root=tmp_path, registry=load_registry())
    assert report.clean
    assert report.rows_scanned == 1


def test_firewall_rejects_an_unknown_generator(tmp_path):
    _write_rows(
        tmp_path,
        [
            {
                "provenance": Provenance(
                    source_type="model_output", generator_model="mystery-model-9"
                ).model_dump(mode="json")
            }
        ],
    )
    report = scan(root=tmp_path, registry=load_registry())
    assert not report.clean
    assert "cannot be checked" in report.violations[0].reason


def test_firewall_flags_rows_with_no_provenance_at_all(tmp_path):
    _write_rows(tmp_path, [{"text": "where did this come from?"}])
    report = scan(root=tmp_path, registry=load_registry())
    assert not report.clean
    assert report.rows_without_provenance == 1


def test_eval_directory_is_exempt(tmp_path):
    """Frontier models belong in the eval matrix. Evaluation is not training."""
    target = tmp_path / "eval" / "results"
    target.mkdir(parents=True)
    (target / "runs.jsonl").write_text(
        json.dumps(
            {
                "provenance": Provenance(
                    source_type="model_output", generator_model="claude-opus-5"
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )
    assert scan(root=tmp_path, registry=load_registry()).clean


def test_allowed_generators_excludes_the_big_three():
    allowed = set(allowed_generators())
    assert "claude-opus-5" not in allowed
    assert "gpt-56-sol" not in allowed
    assert "gemini-20-flash" not in allowed
    assert "groq-oss-120b" in allowed


def test_no_model_id_is_hardcoded_in_source():
    """The claim that swapping a model is a YAML edit, as a gate."""
    violations, files, ids = scan_source()
    assert files > 10, "the scanner found suspiciously few files"
    assert ids > 5
    assert not violations, "\n".join(
        f"{v.path}:{v.line} hardcodes {v.model_id}" for v in violations
    )


def test_content_hash_is_stable_and_order_independent():
    a = content_hash({"b": 2, "a": 1})
    b = content_hash({"a": 1, "b": 2})
    assert a == b
    assert a.startswith("sha256:")


def test_lineage_is_append_only_and_traversable(tmp_path):
    log = LineageLog(tmp_path / "lineage.jsonl")
    log.emit("world:ops@seed7", "world", "opsworld seed 7")
    log.emit("task:ops-T2-0001", "task", "multi-hop lookup", ["world:ops@seed7"])
    log.emit("run:cascade#1", "run", "cascade_default", ["task:ops-T2-0001"])
    log.emit("metric:pass@1", "metric", "pass@1 = 0.978", ["run:cascade#1"])

    ancestors = [n.node_id for n in log.ancestors("metric:pass@1")]
    assert ancestors == [
        "metric:pass@1",
        "run:cascade#1",
        "task:ops-T2-0001",
        "world:ops@seed7",
    ]

    graph = log.as_graph()
    assert len(graph["nodes"]) == 4
    assert {"source": "world:ops@seed7", "target": "task:ops-T2-0001"} in graph["edges"]


def test_lineage_survives_reopening(tmp_path):
    path = tmp_path / "lineage.jsonl"
    LineageLog(path).emit("a", "world", "first")
    LineageLog(path).emit("b", "task", "second", ["a"])
    assert len(LineageLog(path).read()) == 2
