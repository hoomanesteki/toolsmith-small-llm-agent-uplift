"""The harness: how any of this is proved.

The headline metric is computed, never judged. Judges grade what execution
cannot see, and their agreement with human labels is published. Every
configuration runs the same tasks, so comparisons are paired; every cell carries
a bootstrap interval; every pairwise test is multiplicity-corrected; and the
headline column is dollars per SUCCESS, because that is the one that ranks
models honestly.
"""

from toolsmith.harness.calibrate import (
    CalibrationReport,
    HumanLabel,
    calibrate_behaviour,
    calibrate_judges,
    kappa_band,
    read_labels,
)
from toolsmith.harness.grading import TaskScore, grade, pass_at_k
from toolsmith.harness.judges import (
    CHEAP_PANEL,
    DEFAULT_PANEL,
    JudgeCache,
    JudgePanel,
    PanelVerdict,
)
from toolsmith.harness.matrix import (
    Comparison,
    PipelineRow,
    compare_all,
    pareto_frontier,
    summarise_pipeline,
)
from toolsmith.harness.runner import MatrixResult, MatrixRunner, RunConfig, stratified_sample
from toolsmith.harness.stats import (
    Interval,
    align,
    bootstrap_mean,
    cohens_kappa,
    confusion_matrix,
    holm_bonferroni,
    mcnemar,
    paired_bootstrap_difference,
    wilson_interval,
)
from toolsmith.harness.store import (
    Manifest,
    read_matrix,
    read_results,
    read_traces,
    write_judgments,
    write_manifest,
    write_matrix,
    write_results,
    write_traces,
)

__all__ = [
    "CHEAP_PANEL",
    "DEFAULT_PANEL",
    "CalibrationReport",
    "Comparison",
    "HumanLabel",
    "Interval",
    "JudgeCache",
    "JudgePanel",
    "Manifest",
    "MatrixResult",
    "MatrixRunner",
    "PanelVerdict",
    "PipelineRow",
    "RunConfig",
    "TaskScore",
    "align",
    "bootstrap_mean",
    "calibrate_behaviour",
    "calibrate_judges",
    "cohens_kappa",
    "compare_all",
    "confusion_matrix",
    "grade",
    "holm_bonferroni",
    "kappa_band",
    "mcnemar",
    "paired_bootstrap_difference",
    "pareto_frontier",
    "pass_at_k",
    "read_labels",
    "read_matrix",
    "read_results",
    "read_traces",
    "stratified_sample",
    "summarise_pipeline",
    "wilson_interval",
    "write_judgments",
    "write_manifest",
    "write_matrix",
    "write_results",
    "write_traces",
]
