import os
from typing import Any, Optional

from langchain_openai import AzureChatOpenAI

from .base_client import BaseLLMClient, normalize_content, warn_if_truncated
from .validators import validate_model

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "reasoning_effort", "max_tokens",
    "callbacks", "http_client", "http_async_client",
)


class NormalizedAzureChatOpenAI(AzureChatOpenAI):
    """AzureChatOpenAI with normalized content output."""

    def invoke(self, input, config=None, **kwargs):
        response = super().invoke(input, config, **kwargs)
        warn_if_truncated(response, self.model_name)
        return normalize_content(response)


class AzureOpenAIClient(BaseLLMClient):
    """Client for Azure OpenAI deployments.

    Requires environment variables:
        AZURE_OPENAI_API_KEY: API key
        AZURE_OPENAI_ENDPOINT: Endpoint URL (e.g. https://<resource>.openai.azure.com/)
        AZURE_OPENAI_DEPLOYMENT_NAME: Deployment name
        OPENAI_API_VERSION: API version (e.g. 2025-03-01-preview)
    """

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured AzureChatOpenAI instance."""
        self.warn_if_unknown_model()
        allow_env = bool(self.kwargs.get("allow_env_api_key", True))
        if not allow_env and not self.kwargs.get("api_key"):
            # Azure 的 endpoint/key 都习惯从环境变量取；多用户模式下不允许成员
            # 借用服务器凭据。Web UI 暂不暴露 Azure，这里只守住 role_llms 等旁路。
            raise RuntimeError(
                "多用户模式下 Azure 需要显式 api_key；请改用其他供应商或在账户里配置。"
            )

        llm_kwargs = {
            "model": self.model,
            "azure_deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", self.model),
        }

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return NormalizedAzureChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Azure accepts any deployed model name."""
        return True
