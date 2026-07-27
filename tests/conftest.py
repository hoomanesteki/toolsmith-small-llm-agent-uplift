"""Make a fresh clone testable.

The task suite is 7,756 rows and the worlds are SQLite databases. Neither is
committed: they are derived from a seed, and a repository that argues for
reproducing evidence rather than trusting it should not ship the evidence in
binary. `.gitignore` excludes them for the same reason it excludes `build/`.

That left a hole. `pytest` on a fresh checkout failed 29 times with
`FileNotFoundError`, and the message it printed ("run `toolsmith tasks build`")
was correct but arrived after the failure rather than instead of it. CI hit the
same wall on all four matrix cells while the gates and the linters went green,
which is the confusing kind of red: nothing was broken, the machine was simply
empty.

So the suite provisions what it needs. The build is deterministic and takes
about forty seconds, it happens once per session, and it is skipped entirely
when the files are already there, which is the normal case for anyone who has
run `make all`. Set `TOOLSMITH_NO_AUTOBUILD=1` to turn it off and get the old
error back, which is what you want if you are debugging generation itself.
"""

from __future__ import annotations

import os

import pytest

from toolsmith.tasks.store import TASKS_PATH

# The values `toolsmith tasks build` uses. They must match, or the suite would
# regenerate a different dataset from the one the hidden-split hash was sealed
# against and `test_governance.py` would fail for a reason that has nothing to
# do with governance.
SUITE_TOTAL = 8000
SUITE_SEED = 20260726


def _build_suite() -> None:
    """The same four writes `toolsmith tasks build` performs, minus the seal.

    The rejection report is carried onto the summary rather than dropped. It is
    the only record of how many candidate tasks failed oracle verification, and
    a generator with an unrecorded invalid rate is a generator whose output
    nobody can judge.
    """
    from toolsmith.tasks import assign_splits, build_manifest, generate, store
    from toolsmith.tasks.splits import write_manifest

    generated, report = generate(total=SUITE_TOTAL, seed=SUITE_SEED)
    suite = assign_splits(generated)

    summary = store.summarise(suite)
    summary.generation = report.to_dict()

    store.write_tasks(suite.tasks)
    store.write_sample(suite)
    store.write_summary(summary)
    write_manifest(build_manifest(suite), store.MANIFEST_PATH)


@pytest.fixture(scope="session", autouse=True)
def task_suite_exists() -> None:
    """Generate the suite once if this checkout has never built it.

    Deliberately does not reseal the hidden split. The seal is committed and
    the generator is deterministic, so a rebuild reproduces the same hash; a
    fixture that could rewrite it would make the strongest claim in the project
    depend on the weakest file in it.
    """
    if TASKS_PATH.exists() or os.environ.get("TOOLSMITH_NO_AUTOBUILD"):
        return
    _build_suite()
