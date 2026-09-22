from __future__ import annotations
from src.llm.zhipu_provider import ZhipuGLMProvider


def build_llm_provider(settings):
    if settings.llm_provider != "zhipu":
        raise ValueError("Only the real Zhipu GLM provider is supported")
    return ZhipuGLMProvider(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_response_bytes=settings.llm_max_response_bytes,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


PROVIDER_NAMES = ("zhipu",)
