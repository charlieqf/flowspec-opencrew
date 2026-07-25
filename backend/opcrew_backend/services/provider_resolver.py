from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from typing import Any


ABROAD_PROVIDERS = {"openai", "xai", "grok", "gemini", "google", "sync"}
CHINA_PROVIDERS = {"wan", "qwen", "cosyvoice", "minimax", "aliyun", "aliyun_bailian_fun_asr", "dashscope", "kling"}


@dataclass(frozen=True)
class EndpointResolution:
    provider_mode: str
    billing_mode: str
    base_url: str
    auth_ref: str
    proxy_policy: str


def normalize_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value == "google":
        return "gemini"
    if value == "grok":
        return "xai"
    return value


def proxy_policy_for_provider(provider: str) -> str:
    provider = normalize_provider(provider)
    if provider in ABROAD_PROVIDERS:
        return "mihomo"
    return "direct"


def resolve_endpoint(provider: str, model: str, modality: str, auth_ref: str) -> EndpointResolution:
    provider = normalize_provider(provider)
    base_url = {
        "openai": "https://api.openai.com",
        "xai": "https://api.x.ai",
        "gemini": "https://generativelanguage.googleapis.com",
        "wan": "https://dashscope.aliyuncs.com",
        "qwen": "https://dashscope.aliyuncs.com",
        "cosyvoice": "https://dashscope.aliyuncs.com",
        "minimax": "https://dashscope.aliyuncs.com",
        "aliyun_bailian_fun_asr": "https://dashscope.aliyuncs.com",
        "kling": "https://api-beijing.klingai.com",
        "sync": "https://api.sync.so",
    }.get(provider, "")
    return EndpointResolution(
        provider_mode="local_box",
        billing_mode="local_usage_only",
        base_url=base_url,
        auth_ref=auth_ref,
        proxy_policy=proxy_policy_for_provider(provider),
    )


def mihomo_proxy_url() -> str:
    return os.environ.get("OPENCREW_MIHOMO_PROXY_URL", "http://127.0.0.1:7890").strip()


def urlopen(req: urllib.request.Request, *, timeout: int | float, proxy_policy: str = "direct") -> Any:
    if proxy_policy != "mihomo":
        return urllib.request.urlopen(req, timeout=timeout)
    proxy_url = mihomo_proxy_url()
    if not proxy_url:
        return urllib.request.urlopen(req, timeout=timeout)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return opener.open(req, timeout=timeout)
