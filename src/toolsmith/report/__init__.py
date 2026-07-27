"""Every published artifact, regenerated from results.jsonl.

Nothing in the report is typed by hand. If a number in the prose and a number
in the results file ever disagree, the build fails rather than the reader being
misled.
"""

from toolsmith.report.build import GENERATED, ReportContext, build, key_numbers, load_context

__all__ = ["GENERATED", "ReportContext", "build", "key_numbers", "load_context"]
