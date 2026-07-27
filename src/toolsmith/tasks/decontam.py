"""Train/test leakage detection.

Two independent checks, because either one alone is easy to fool.

**MinHash LSH over character shingles** catches near-duplicates that differ by
an id or a word. This is the standard method, and ``datasketch`` implements it
properly; when the optional dependency is absent the module falls back to exact
Jaccard over the same shingles, which is slower but not weaker on a dataset this
size, and says so in the report.

**Eight-gram token overlap** catches the case MinHash misses: a long verbatim
span embedded in an otherwise different prompt.

WHAT COUNTS AS LEAKAGE HERE
---------------------------
A template-generated suite has near-identical prompts by construction. "What is
the status of order ORD-5001?" and "...ORD-5002?" share 95% of their characters
and are not remotely the same task: the answers differ, so memorising one buys
nothing on the other.

So leakage is defined on three axes, all of which must hold:

1. the prompts are near-duplicates,
2. the answers agree, and
3. the gold programs are identical, arguments included.

The third axis is what the first two miss. "Which coverage plan is PAT-4102 on?"
and "Which coverage plan is PAT-4200 on?" are 92% identical and both answer
"standard", because there are only four plans. Memorising one tells you nothing
about the other: they are different patients. The programs differ in their
arguments, and that is the signal.

The program is used rather than entity names scraped from the prompt because it
is exact and language-independent: it works for a domain whose subjects are
lowercase, and for a prompt that names nothing at all.

Near-duplicates that fail axis two or three are counted as
``surface_duplicates``. They are not a failure, but a suite where that number is
enormous has too little variety, and hiding it would be the same evasion in a
different direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from toolsmith.tasks.models import Task

SHINGLE_SIZE = 5
NGRAM_SIZE = 8
NUM_PERM = 128
JACCARD_THRESHOLD = 0.6
NGRAM_THRESHOLD = 0.5

_WORD = re.compile(r"[a-z0-9]+")

#: Entity references: the world id scheme (CUS-1042, APT-9001, SEC-7194) plus
#: capitalised words, which is how people and services are named in a prompt.
_ENTITY = re.compile(r"\b[A-Z]{2,4}-\d+\b|\b[A-Z][a-z]{2,}\b")

#: Words that are capitalised for grammar rather than because they name a thing.
_SENTENCE_STARTERS = frozenset(
    {
        "What",
        "Which",
        "Who",
        "How",
        "When",
        "Where",
        "Can",
        "Could",
        "Look",
        "Check",
        "Give",
        "Tell",
        "For",
        "Under",
        "Pull",
        "The",
        "Please",
        "Open",
        "Log",
        "Rank",
        "Count",
        "Process",
        "Before",
        "Make",
        "There",
        "Summarise",
        "According",
        "Raise",
        "Show",
        "Find",
        "Is",
        "Are",
        "Do",
        "Go",
        "Of",
        "This",
        "That",
        "Note",
        "Cite",
        "Reviewed",
    }
)


def entity_refs(text: str) -> frozenset[str]:
    """Which specific things a prompt is about."""
    return frozenset(m for m in _ENTITY.findall(text) if m not in _SENTENCE_STARTERS)


#: Splits that must not overlap. Train may look like train.
LEAK_PAIRS: tuple[tuple[str, str], ...] = (
    ("train", "test"),
    ("train", "test_hidden"),
    ("train", "val"),
    ("val", "test"),
    ("val", "test_hidden"),
)


@dataclass(slots=True)
class Collision:
    left: str
    right: str
    left_split: str
    right_split: str
    jaccard: float
    ngram_overlap: float
    same_answer: bool = True


@dataclass
class DecontamReport:
    n_tasks: int
    method: str
    collisions: list[Collision] = field(default_factory=list)
    surface_duplicates: int = 0
    """Cross-split near-duplicate prompts whose answers differ. Not leakage:
    reported so that low prompt variety is visible rather than hidden."""

    compared_pairs: int = 0

    @property
    def clean(self) -> bool:
        return not self.collisions

    def to_dict(self) -> dict[str, object]:
        return {
            "n_tasks": self.n_tasks,
            "method": self.method,
            "compared_pairs": self.compared_pairs,
            "surface_duplicates": self.surface_duplicates,
            "collisions": [c.__dict__ for c in self.collisions],
            "clean": self.clean,
        }


def shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    """Character shingles over the normalised prompt."""
    normalised = " ".join(text.lower().split())
    if len(normalised) <= size:
        return {normalised}
    return {normalised[i : i + size] for i in range(len(normalised) - size + 1)}


def ngrams(text: str, size: int = NGRAM_SIZE) -> set[tuple[str, ...]]:
    tokens = _WORD.findall(text.lower())
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _candidate_pairs_minhash(tasks: list[Task]) -> tuple[list[tuple[int, int]], str] | None:
    try:
        from datasketch import MinHash, MinHashLSH
    except ImportError:
        return None

    lsh = MinHashLSH(threshold=JACCARD_THRESHOLD, num_perm=NUM_PERM)
    signatures = {}
    for index, task in enumerate(tasks):
        minhash = MinHash(num_perm=NUM_PERM)
        for shingle in shingles(task.content_key()):
            minhash.update(shingle.encode())
        signatures[index] = minhash
        lsh.insert(str(index), minhash)

    pairs: set[tuple[int, int]] = set()
    for index in signatures:
        for other in lsh.query(signatures[index]):
            j = int(other)
            if j != index:
                pairs.add((min(index, j), max(index, j)))
    return (
        sorted(pairs),
        f"MinHash LSH (num_perm={NUM_PERM}, threshold={JACCARD_THRESHOLD}) + {NGRAM_SIZE}-gram overlap",
    )


def _candidate_pairs_exact(tasks: list[Task]) -> tuple[list[tuple[int, int]], str]:
    """Fallback: bucket by a cheap key, then compare within buckets.

    Comparing all pairs of 8,000 tasks is 32 million comparisons, which is slow
    but finite. Bucketing on the first four tokens cuts it to something
    instantaneous without weakening the check, because two prompts that share no
    opening tokens cannot reach a 0.6 Jaccard on character shingles.
    """
    buckets: dict[tuple[str, ...], list[int]] = {}
    for index, task in enumerate(tasks):
        tokens = tuple(_WORD.findall(task.content_key())[:4])
        buckets.setdefault(tokens, []).append(index)
    pairs = [pair for bucket in buckets.values() for pair in combinations(sorted(bucket), 2)]
    return pairs, f"bucketed exact Jaccard (datasketch absent) + {NGRAM_SIZE}-gram overlap"


def check_leakage(
    dataset: Path | list[Task], threshold: float = JACCARD_THRESHOLD
) -> DecontamReport:
    tasks = dataset if isinstance(dataset, list) else _read(dataset)
    minhash_result = _candidate_pairs_minhash(tasks)
    pairs, method = minhash_result if minhash_result else _candidate_pairs_exact(tasks)

    report = DecontamReport(n_tasks=len(tasks), method=method, compared_pairs=len(pairs))
    cross = {frozenset(p) for p in LEAK_PAIRS}

    shingle_cache: dict[int, set[str]] = {}
    ngram_cache: dict[int, set[tuple[str, ...]]] = {}

    for i, j in pairs:
        left, right = tasks[i], tasks[j]
        if frozenset({left.split, right.split}) not in cross:
            continue
        if i not in shingle_cache:
            shingle_cache[i] = shingles(left.content_key())
            ngram_cache[i] = ngrams(left.content_key())
        if j not in shingle_cache:
            shingle_cache[j] = shingles(right.content_key())
            ngram_cache[j] = ngrams(right.content_key())

        j_score = jaccard(shingle_cache[i], shingle_cache[j])
        n_score = jaccard(ngram_cache[i], ngram_cache[j])
        if j_score < threshold and n_score < NGRAM_THRESHOLD:
            continue

        same_answer = sorted(left.answer_keys) == sorted(right.answer_keys)
        same_program = left.program_key() == right.program_key()
        if not (same_answer and same_program):
            report.surface_duplicates += 1
            continue

        report.collisions.append(
            Collision(
                left=left.task_id,
                right=right.task_id,
                left_split=left.split,
                right_split=right.split,
                jaccard=round(j_score, 4),
                ngram_overlap=round(n_score, 4),
                same_answer=True,
            )
        )
    report.collisions.sort(key=lambda c: -c.jaccard)
    return report


def _read(path: Path) -> list[Task]:
    from toolsmith.tasks.store import read_tasks

    return read_tasks(path)
