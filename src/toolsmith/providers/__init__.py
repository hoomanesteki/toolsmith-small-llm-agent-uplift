"""Provider layer.

Everything above this package speaks :class:`LLMRequest` and
:class:`LLMResponse` and knows nothing about any vendor's wire format. That is
the boundary that lets ``configs/pipelines/*.yaml`` swap a model without a code
change.
"""

from toolsmith.providers.base import (
    LLMRequest,
    LLMResponse,
    Message,
    Provider,
    ProviderError,
    RateLimiter,
    ToolCall,
    ToolSchema,
    cache_hit_tokens,
    estimate_message_tokens,
    estimate_tokens,
)
from toolsmith.providers.registry import (
    Availability,
    ProviderFactory,
    ProviderMode,
    ProviderStatus,
    describe_availability,
)
from toolsmith.providers.simulated import SimContext, SimulatedProvider

__all__ = [
    "Availability",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "Provider",
    "ProviderError",
    "ProviderFactory",
    "ProviderMode",
    "ProviderStatus",
    "RateLimiter",
    "SimContext",
    "SimulatedProvider",
    "ToolCall",
    "ToolSchema",
    "cache_hit_tokens",
    "describe_availability",
    "estimate_message_tokens",
    "estimate_tokens",
]
