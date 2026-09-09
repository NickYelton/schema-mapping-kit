from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "replay"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    embedding_model: str = "minishlab/potion-retrieval-32M"

    duckdb_path: Path = Path("data/catalog.duckdb")
    artifacts_dir: Path = Path("artifacts")
    schemas_dir: Path = Path("schemas")
    samples_dir: Path = Path("samples")
    llm_fixtures_dir: Path = Path("fixtures/llm")

    cors_origins: list[str] = ["http://localhost:5173"]

    def resolve(self, value: Path) -> Path:
        return value if value.is_absolute() else REPO_ROOT / value

    @property
    def duckdb_file(self) -> Path:
        return self.resolve(self.duckdb_path)

    @property
    def artifacts(self) -> Path:
        return self.resolve(self.artifacts_dir)

    @property
    def schemas(self) -> Path:
        return self.resolve(self.schemas_dir)

    @property
    def samples(self) -> Path:
        return self.resolve(self.samples_dir)

    @property
    def llm_fixtures(self) -> Path:
        return self.resolve(self.llm_fixtures_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
