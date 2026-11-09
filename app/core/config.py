from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root, so .env and the data folders are found no matter which directory the app is started from.
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # Default is a free local model, so a missing .env can never trigger a paid call.
    llm_provider: str = "ollama"      # ollama | groq | gemini | openai | anthropic
    llm_model: str = ""               # empty = the provider's default model (see app/agent/llm.py)
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"

    llm_cache: bool = True
    cache_dir: str = str(ROOT / ".cache" / "llm")
    data_dir: str = str(ROOT / "data")

    use_mcp: bool = True
    node_binary: str = "node"
    sandbox_mode: str = "subprocess"  # subprocess | docker
    sandbox_timeout: int = 30         # whole test run
    per_test_timeout: int = 5         # each individual test
    sandbox_image: str = "code-review-sandbox"
    max_test_attempts: int = 2

    chroma_dir: str = str(ROOT / ".chroma")
    knowledge_dir: str = str(ROOT / "knowledge")
    rag_top_k: int = 4
    max_code_chars: int = 20000


settings = Settings()
