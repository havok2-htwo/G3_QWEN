from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    queued = 'queued'
    warming = 'warming'
    running = 'running'
    streaming = 'streaming'
    cancelling = 'cancelling'
    completed = 'completed'
    failed = 'failed'
    cancelled = 'cancelled'


class TaskType(str, Enum):
    custom_voice = 'CustomVoice'
    voice_design = 'VoiceDesign'
    base = 'Base'


class SpeechRequest(BaseModel):
    input: str | None = None
    model: str | None = None
    voice: str | None = None
    task_type: TaskType | None = None
    language: str | None = None
    instructions: str | None = None
    response_format: str = 'wav'
    speed: float = 1.0
    stream: bool = False
    ref_audio: str | None = None
    ref_text: str | None = None
    x_vector_only_mode: bool = False
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobMetrics(BaseModel):
    queue_wait_ms: int | None = None
    model_warm_ms: int | None = None
    ttfa_ms: int | None = None
    job_wall_ms: int | None = None
    audio_duration_ms: int | None = None
    realtime_x: float | None = None
    output_bytes: int | None = None
    batch_count: int | None = None
    sentences_total: int | None = None
    sentences_rendered: int | None = None


class SpeechJobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    queue_position: int
    eta_ms: int


class SynthesisResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    model: str | None = None
    task_type: TaskType | None = None
    voice: str | None = None
    sample_rate: int
    metrics: JobMetrics = Field(default_factory=JobMetrics)
    audio_url: str | None = None
    error_message: str | None = None


class JobListItem(BaseModel):
    job_id: str
    status: JobStatus
    model: str | None = None
    task_type: TaskType | None = None
    voice: str | None = None
    input_preview: str
    queue_position: int
    eta_ms: int
    created_at: datetime
    updated_at: datetime
    metrics: JobMetrics = Field(default_factory=JobMetrics)
    error_message: str | None = None


class JobDetailResponse(JobListItem):
    started_at: datetime | None = None
    first_audio_at: datetime | None = None
    completed_at: datetime | None = None
    sentences_total: int | None = None
    batch_count: int | None = None


class AdminKeyMetadata(BaseModel):
    key_id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None = None


class AdminKeyResponse(BaseModel):
    admin_key: AdminKeyMetadata


class AdminKeyRotateResponse(BaseModel):
    admin_key: AdminKeyMetadata
    token: str


class VoiceProfileCreateResponse(BaseModel):
    voice_id: str
    name: str
    source: str
    created_at: datetime


class VoiceProfileListItem(BaseModel):
    voice_id: str
    name: str
    source: str
    created_at: datetime | None = None


class VoiceCatalogResponse(BaseModel):
    voices: list[VoiceProfileListItem] = Field(default_factory=list)


class ModelInfo(BaseModel):
    model_id: str
    loaded: bool
    active: bool
    task_types: list[TaskType]


class StatRolling(BaseModel):
    ttfa_ms_avg: float | None = None
    queue_wait_ms_avg: float | None = None
    job_wall_ms_avg: float | None = None
    realtime_x_avg: float | None = None


class StatGlobal(BaseModel):
    jobs_total: int
    audio_seconds_total: float
    realtime_x_avg: float | None = None


class StatsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_model: str
    queue_depth: int
    worker_state: str
    rolling: StatRolling
    global_: StatGlobal = Field(alias='global')


class GpuStatsResponse(BaseModel):
    name: str
    memory_used_mb: int
    memory_total_mb: int
    utilization_percent: int
    temperature_c: int | None = None


class TranscriptionResponse(BaseModel):
    transcription: str
    voice_vector: list[float] | None = None


class ServerSettingsResponse(BaseModel):
    model_directory: str
    default_model: str
    default_voice: str
    whisper_base_url: str | None = None
    whisper_path: str
    retention_days: int
    queue_limit: int
    runtime_backend: str
    allow_model_downloads: bool
    preferred_device: str
    attention_implementation: str
    torch_dtype: str
    sample_rate: int
    poll_interval_ms: int
    theme: str
    built_in_voices: list[str] = Field(default_factory=list)
    sentence_chunking: bool
    short_sentence_merge_max_chars: int
    following_sentence_merge_min_chars: int
    max_parallel_requests: int
    max_batch_size: int
    batch_wait_ms: int
    stream_chunk_ms: int
    stream_prebuffer_ms: int


class ServerSettingsUpdateRequest(BaseModel):
    model_directory: str | None = None
    default_model: str | None = None
    default_voice: str | None = None
    whisper_base_url: str | None = None
    whisper_path: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    queue_limit: int | None = Field(default=None, ge=1, le=512)
    allow_model_downloads: bool | None = None
    preferred_device: str | None = None
    attention_implementation: str | None = None
    torch_dtype: str | None = None
    poll_interval_ms: int | None = Field(default=None, ge=250, le=5000)
    theme: str | None = None
    sentence_chunking: bool | None = None
    short_sentence_merge_max_chars: int | None = Field(default=None, ge=0, le=512)
    following_sentence_merge_min_chars: int | None = Field(default=None, ge=0, le=1024)
    max_parallel_requests: int | None = Field(default=None, ge=1, le=64)
    max_batch_size: int | None = Field(default=None, ge=1, le=64)
    batch_wait_ms: int | None = Field(default=None, ge=0, le=1000)
    stream_chunk_ms: int | None = Field(default=None, ge=20, le=1000)
    stream_prebuffer_ms: int | None = Field(default=None, ge=0, le=5000)


class DashboardOverview(BaseModel):
    active_model: str
    queue_depth: int
    active_requests: int
    worker_state: str
    ttfa_ms_avg: float | None = None
    queue_wait_ms_avg: float | None = None
    job_wall_ms_avg: float | None = None
    realtime_x_avg: float | None = None
    jobs_total: int
    audio_seconds_total: float
    gpu_name: str
    gpu_memory_used_mb: int
    gpu_memory_total_mb: int
    gpu_utilization_pct: int
    gpu_temperature_c: int | None = None


class BatchSnapshot(BaseModel):
    batch_id: str
    model_id: str
    task_type: TaskType
    voice: str | None = None
    language: str | None = None
    size: int
    started_at: datetime
    request_ids: list[str] = Field(default_factory=list)
    sentence_indices: list[int] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    overview: DashboardOverview
    settings: ServerSettingsResponse
    models: list[ModelInfo] = Field(default_factory=list)
    voices: list[VoiceProfileListItem] = Field(default_factory=list)
    jobs: list[JobListItem] = Field(default_factory=list)
    admin_key: AdminKeyMetadata
    current_batch: BatchSnapshot | None = None
    recent_batches: list[BatchSnapshot] = Field(default_factory=list)
