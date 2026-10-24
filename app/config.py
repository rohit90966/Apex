from functools import lru_cache
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for VerityQA."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Primary LLM Provider Configuration (Groq is default)
    llm_provider: str = Field(default="groq", description="LLM provider: 'groq' (default), 'gemini', or 'ollama'")
    groq_api_key: Optional[str] = Field(default=None, description="Groq API key")
    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Supported Groq model ID")
    groq_temperature: float = Field(default=0.1, description="Temperature for Groq generation")
    google_api_key: Optional[str] = Field(default=None, description="Google Gemini API key")
    gemini_model: str = Field(default="gemini-flash-latest", description="Supported Gemini model ID")
    gemini_temperature: float = Field(default=0.1, description="Temperature for Gemini generation")
    llm_timeout_seconds: float = Field(default=120.0, description="Universal LLM request timeout in seconds")
    llm_max_retries: int = Field(default=3, description="Max retry attempts for transient LLM errors")

    # Local Ollama Provider Configuration (retained as configurable option)
    ollama_base_url: str = Field(default="http://localhost:11434", description="Base URL of local Ollama API")
    ollama_model: str = Field(default="qwen2.5:7b", description="Model name for test planning")
    # Patience-first local inference: None (default) means NO read/inference deadline. The local model
    # is allowed to take as long as it needs. Only the TCP connect phase is bounded so that a stopped
    # Ollama server is reported as a connection failure instead of hanging forever.
    ollama_timeout_seconds: Optional[float] = Field(default=None, description="Ollama inference timeout in seconds (None = no artificial timeout)")
    ollama_connect_timeout_seconds: float = Field(default=10.0, description="TCP connect timeout for reaching the Ollama server")
    ollama_num_predict: int = Field(default=1500, description="Max tokens for Ollama generation")
    ollama_num_ctx: int = Field(default=4096, description="Context window size for Ollama")
    ollama_max_format_retries: int = Field(default=1, description="Max 1-step JSON format repair attempts")

    verityqa_artifacts_dir: str = Field(default="artifacts", description="Directory to store screenshots and artifacts")
    verityqa_runs_dir: str = Field(default="data/runs", description="Directory where investigation runs, reports, and generated test cases are persisted")
    verityqa_headless: bool = Field(default=True, description="Whether to run Playwright in headless mode")
    verityqa_max_test_cases: int = Field(default=15, description="Maximum test cases generated per test plan")
    verityqa_allowed_hosts: str = Field(
        default="127.0.0.1,localhost",
        description="Comma-separated allowed target hostnames/IPs for inspection",
    )

    # Step 3: Embeddings & Vector Knowledge Store
    ollama_embed_model: str = Field(default="nomic-embed-text", description="Ollama embedding model")
    chroma_persist_dir: str = Field(default="data/chroma", description="Path to persist ChromaDB collection")

    # Step 3: Deep Website Investigation
    investigation_max_pages: int = Field(default=15, description="Maximum pages discovered during deep crawl")
    investigation_max_depth: int = Field(default=3, description="Maximum crawl depth from starting URL")
    investigation_nav_timeout_ms: int = Field(default=10000, description="Page navigation timeout in ms")

    # Phase 1: Generic Autonomous Exploration budgets
    exploration_max_depth: int = Field(default=3, description="Maximum state-graph depth for autonomous exploration")
    exploration_max_actions: int = Field(default=30, description="Maximum actions taken during one exploration session")
    exploration_max_pages: int = Field(default=10, description="Maximum distinct pages (states) visited during exploration")
    exploration_max_time_seconds: float = Field(default=120.0, description="Wall-clock time limit for exploration session (seconds)")

    # Stateful investigation budgets. Browser-execution time EXCLUDES time spent waiting for LLM inference.
    # A value <= 0 for investigation_max_browser_seconds means unlimited duration.
    investigation_max_browser_seconds: float = Field(default=300.0, description="Browser-execution budget for one investigation (<=0 = unlimited)")
    investigation_max_actions: int = Field(default=25, description="Maximum probe actions per investigation run (<=0 = adaptive unlimited)")
    investigation_adaptive_mode: bool = Field(default=True, description="When true, probe count is not a mandatory stop condition; depth is guided by information gain and convergence")
    investigation_max_unproductive_probes: int = Field(default=6, description="Stop after N consecutive probes yielding no new state, route, or UI mutation")
    investigation_emergency_ceiling: int = Field(default=100, description="Absolute safety probe limit to avoid runaway execution")
    investigation_checkpoint_dir: str = Field(default="artifacts/checkpoints", description="Directory for resumable investigation checkpoints")

    @property
    def allowed_hosts_list(self) -> List[str]:
        return [h.strip().lower() for h in self.verityqa_allowed_hosts.split(",") if h.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
