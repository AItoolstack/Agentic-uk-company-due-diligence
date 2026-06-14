"""
config.py
Centralised application settings loaded from environment variables / .env file.
All other modules should import from here -- never read os.environ directly.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM -- model-agnostic via LangChain init_chat_model
    chat_model_provider: str = Field(default="openai", alias="CHAT_MODEL_PROVIDER")
    chat_model: str = Field(default="gpt-4o", alias="CHAT_MODEL")
    openai_temperature: float = Field(default=0.0, alias="OPENAI_TEMPERATURE")
    chat_model_fast: str = Field(default="", alias="CHAT_MODEL_FAST")

    # Provider API keys
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    # Companies House
    companies_house_api_key: str = Field(default="", alias="COMPANIES_HOUSE_API_KEY")
    companies_house_base_url: str = Field(
        default="https://api.company-information.service.gov.uk",
        alias="COMPANIES_HOUSE_BASE_URL",
    )

    # FCA Register
    fca_api_key: str = Field(default="", alias="FCA_API_KEY")
    fca_base_url: str = Field(
        default="https://register.fca.org.uk/services/V0.1",
        alias="FCA_BASE_URL",
    )

    # News API
    news_api_key: str = Field(default="", alias="NEWS_API_KEY")
    news_api_base_url: str = Field(
        default="https://newsapi.org/v2",
        alias="NEWS_API_BASE_URL",
    )

    # Brave Search API
    brave_search_api_key: str = Field(default="", alias="BRAVE_SEARCH_API_KEY")

    # OpenSanctions API (optional -- free tier works without a key)
    # https://www.opensanctions.org/docs/api/
    open_sanctions_api_key: str = Field(default="", alias="OPEN_SANCTIONS_API_KEY")
    open_sanctions_base_url: str = Field(
        default="https://api.opensanctions.org",
        alias="OPEN_SANCTIONS_BASE_URL",
    )

    # Feature flags
    use_mock_data: bool = Field(default=True, alias="USE_MOCK_DATA")
    max_followup_iterations: int = Field(
        default=2,
        ge=1,
        alias="MAX_FOLLOWUP_ITERATIONS",
    )
    confidence_threshold: float = Field(default=0.6, alias="CONFIDENCE_THRESHOLD")
    cors_allowed_origins: str = Field(
        default="http://localhost:8000,http://127.0.0.1:8000",
        alias="CORS_ALLOWED_ORIGINS",
    )

    # Tracing / observability
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str = Field(default="", alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(
        default="agentic_company_research_poc",
        alias="LANGCHAIN_PROJECT",
    )


settings = Settings()
