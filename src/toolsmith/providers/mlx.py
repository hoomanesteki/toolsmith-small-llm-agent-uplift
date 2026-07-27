"""Local Apple-silicon inference, via an ``mlx_lm`` server on localhost.

Deliberately a thin delegate to the OpenAI-compatible adapter rather than an
``import mlx_lm``. Three reasons, and they are all about keeping the repository
honest and small:

* CI never tries to load a 2.5 GB model.
* ``uv sync`` on a Linux box does not fail on an Apple-only dependency.
* The local row is exercised by exactly the same code path as every hosted row,
  so "runs on my laptop" is a config line rather than a special case.

Start the server with::

    uv run mlx_lm.server --model mlx-community/Qwen3.5-4B-Instruct-4bit --port 8080

Storage policy: one 4B model at 4-bit, roughly 2.5 GB. Nothing 8B or larger is
downloaded to the machine. Total local footprint stays under 6 GB.
"""

from __future__ import annotations

import httpx

from toolsmith.config import ModelSpec
from toolsmith.providers.base import (
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    RateLimiter,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"


class MLXProvider(Provider):
    provenance = "live"
    name = "mlx"

    def __init__(
        self,
        spec: ModelSpec,
        limiter: RateLimiter | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(spec, limiter)
        self.base_url = base_url
        self.timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        from toolsmith.providers.openai_compat import OpenAICompatProvider

        delegate = OpenAICompatProvider(
            self.spec,
            self.limiter,
            client=httpx.Client(base_url=self.base_url, timeout=self.timeout),
        )
        try:
            return delegate.complete(request)
        except ProviderError as exc:
            raise ProviderError(
                f"local MLX server unreachable at {self.base_url}. Start it with:\n"
                f"  uv run mlx_lm.server --model {self.spec.model_id} --port 8080"
            ) from exc
        finally:
            delegate.close()
