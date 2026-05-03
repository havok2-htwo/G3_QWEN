from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_ROOT = PROJECT_ROOT / 'models'
DEFAULT_FRONTEND_DIST = PROJECT_ROOT / 'frontend' / 'dist'
DEFAULT_DATA_DIR = PROJECT_ROOT / 'data'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='QWEN_TTS_', case_sensitive=False)

    app_name: str = 'Qwen TTS Server'
    host: str = '0.0.0.0'
    port: int = 8088
    admin_api_key: str = Field(default='dev-admin-key')
    startup_admin_key: str = ''
    startup_admin_key_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    whisper_base_url: str | None = None
    whisper_path: str = '/transcribe/'
    active_model: str = 'Qwen3-TTS-12Hz-0.6B-CustomVoice'
    default_voice: str = 'Ryan'
    supported_models: list[str] = Field(
        default_factory=lambda: [
            'Qwen3-TTS-12Hz-0.6B-CustomVoice',
            'Qwen3-TTS-12Hz-1.7B-CustomVoice',
            'Qwen3-TTS-12Hz-1.7B-VoiceDesign',
            'Qwen3-TTS-12Hz-0.6B-Base',
            'Qwen3-TTS-12Hz-1.7B-Base',
        ]
    )
    built_in_voices: list[str] = Field(
        default_factory=lambda: ['Ryan', 'Vivian', 'Serena', 'Aiden', 'Mia', 'Nova', 'Ava']
    )
    models_root_dir: Path = Field(default=DEFAULT_MODELS_ROOT)
    frontend_dist_dir: Path = Field(default=DEFAULT_FRONTEND_DIST)
    data_dir: Path = Field(default=DEFAULT_DATA_DIR)
    runtime_backend: str = 'qwen'
    allow_model_downloads: bool = False
    enable_cpu_offload: bool = False
    compile_model: bool = False
    warmup_on_startup: bool = True
    preferred_device: str = 'cuda:0'
    attention_implementation: str = 'flash_attention_2'
    torch_dtype: str = 'bfloat16'
    max_queue_size: int = 32
    sample_rate: int = 24_000
    channels: int = 1
    retention_days: int = 7
    benchmark_dataset_default: str = 'de_standard_v1'
    frontend_poll_interval_ms: int = 1000
    frontend_theme: str = 'onyx'
    sentence_chunking: bool = True
    short_sentence_merge_max_chars: int = 30
    following_sentence_merge_min_chars: int = 20
    max_parallel_requests: int = 6
    max_batch_size: int = 8
    batch_wait_ms: int = 35
    stream_chunk_ms: int = 140
    stream_prebuffer_ms: int = 0

    @field_validator('models_root_dir', mode='before')
    @classmethod
    def validate_models_root_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        return Path(value).expanduser()

    @field_validator('frontend_dist_dir', mode='before')
    @classmethod
    def validate_frontend_dist_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        return Path(value).expanduser()

    @field_validator('data_dir', mode='before')
    @classmethod
    def validate_data_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        return Path(value).expanduser()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.models_root_dir.mkdir(parents=True, exist_ok=True)
    return settings
