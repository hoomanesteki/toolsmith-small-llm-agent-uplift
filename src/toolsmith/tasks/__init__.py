"""Task generation, oracle execution, splits and decontamination.

Ground truth is computed, never written by hand and never judged by a model.
Every task carries an oracle program expressed in the shared verb grammar;
running it in a fresh sandbox produces the answer, the state diff and the call
sequence that the harness grades against.
"""

from toolsmith.tasks.decontam import DecontamReport, check_leakage
from toolsmith.tasks.generate import generate
from toolsmith.tasks.models import (
    TIER_PURPOSE,
    InjectionSpec,
    OracleStep,
    Task,
    TaskSuite,
    Tier,
)
from toolsmith.tasks.oracle import GenerationReport, execute_program, verify_task
from toolsmith.tasks.splits import (
    assign_splits,
    build_manifest,
    hidden_digest,
    seal_hidden_split,
    verify_hidden_split,
)
from toolsmith.tasks.store import (
    MANIFEST_PATH,
    SEAL_PATH,
    TASKS_PATH,
    load_split,
    read_tasks,
    summarise,
    write_tasks,
)

__all__ = [
    "MANIFEST_PATH",
    "SEAL_PATH",
    "TASKS_PATH",
    "TIER_PURPOSE",
    "DecontamReport",
    "GenerationReport",
    "InjectionSpec",
    "OracleStep",
    "Task",
    "TaskSuite",
    "Tier",
    "assign_splits",
    "build_manifest",
    "check_leakage",
    "execute_program",
    "generate",
    "hidden_digest",
    "load_split",
    "read_tasks",
    "seal_hidden_split",
    "summarise",
    "verify_hidden_split",
    "verify_task",
    "write_tasks",
]
