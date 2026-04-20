"""运行时配置。

通过 `pydantic-settings` 从环境变量 / `.env` 文件加载。未来 Secrets Manager 落地后,此处
会改为从 Secrets 抽象接口读取,而不是直接读 env(见 P0-020)。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """平台级配置。

    所有字段均从环境变量读取,`.env` 仅作本地开发兜底。生产部署通过 K8s secret / Vault 注入。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(
        ...,
        description="OpenAI API Key。Agents SDK 调用模型的凭证。",
    )
    openai_model: str = Field(
        default="gpt-4.1-mini",
        description="默认模型名。hello-world / 低成本任务用,业务 agent 可按需覆盖。",
    )


def load_settings() -> Settings:
    """显式加载入口。

    封装成函数便于测试时 monkeypatch,以及未来接入 Secrets Manager 时切换来源。
    """
    return Settings()  # type: ignore[call-arg]
