"""Four improvement tracks, measured on one axis.

The order is C, B, A, D: the free lever first, the highest-evidence one second,
the token-cost one third, the GPU one last and only if it is still worth it.
Every track tunes on validation and reports on test, and a track that shows no
gain publishes the null rather than quietly disappearing.
"""

from toolsmith.optimize import (
    track_a_prompts,
    track_b_router,
    track_c_context,
    track_d_lora,
)
from toolsmith.optimize.base import OPTIMIZE_DIR, TrackResult, read_all, read_track, relative

#: Run order. The list is the argument.
TRACKS = {
    "c": track_c_context,
    "b": track_b_router,
    "a": track_a_prompts,
    "d": track_d_lora,
}

__all__ = [
    "OPTIMIZE_DIR",
    "TRACKS",
    "TrackResult",
    "read_all",
    "read_track",
    "relative",
    "track_a_prompts",
    "track_b_router",
    "track_c_context",
    "track_d_lora",
]
