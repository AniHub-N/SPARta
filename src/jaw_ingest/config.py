from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class IngestConfig(BaseSettings):
    cache_dir: Path = Field(default=Path(".cache"), description="Directory for extraction cache files.")
    output_dir: Path = Field(default=Path("output"), description="Default output directory for generated artifacts.")
    log_level: str = Field(default="INFO", description="Root logging level.")
    cache_enabled: bool = Field(default=True, description="Enable or disable document caching.")
    config_file: Path | None = Field(default=None, description="Optional config file path.")
    extraction_schema_version: str = Field(default="1.0", description="Extraction schema version for fact extraction.")
    vlm_provider: str = Field(default="none", description="Vision model provider name.")
    vlm_model: str | None = Field(default=None, description="Vision model identifier.")
    vlm_endpoint: str | None = Field(default=None, description="Vision model API endpoint.")
    vlm_temperature: float = Field(default=0.0, description="Temperature for model sampling.")
    vlm_timeout: int = Field(default=30, description="Timeout seconds for VLM inference.")
    vlm_retry_count: int = Field(default=1, description="Retry count for VLM calls.")
    vlm_max_image_resolution: int = Field(default=1600, description="Maximum resolution for rendered page images.")
    vlm_token_limit: int = Field(default=8192, description="Maximum token limit for VLM requests.")
    llm_provider: str = Field(
        default="none",
        description="Semantic extraction LLM provider: 'none' (default, no calls made) or "
        "'openai_compatible' (any server implementing the OpenAI chat-completions HTTP contract "
        "- OpenAI, Groq, a local Ollama/vLLM OpenAI-compatible endpoint, etc.).",
    )
    llm_base_url: str | None = Field(default=None, description="Base URL for the LLM provider's API.")
    llm_api_key: str | None = Field(default=None, description="API key for the LLM provider, if required.")
    llm_model: str | None = Field(default=None, description="Model identifier to request from the LLM provider.")
    llm_timeout: int = Field(default=30, description="Timeout seconds for semantic extraction LLM calls.")

    class Config:
        env_prefix = "JAW_"
        env_file = ".env"
        env_file_encoding = "utf-8"


Settings = IngestConfig()


def configure_logging(level: str | None = None) -> None:
    root_level = level or Settings.log_level
    logging.basicConfig(
        level=root_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("openpyxl").setLevel(logging.WARNING)
