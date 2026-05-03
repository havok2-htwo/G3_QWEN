from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ..domain.models import (
    AdminKeyMetadata,
    AdminKeyResponse,
    AdminKeyRotateResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    BenchmarkRunCreateRequest,
    BenchmarkRunResponse,
    GpuStatsResponse,
    JobDetailResponse,
    JobListItem,
    JobStatus,
    ModelInfo,
    ServerSettingsResponse,
    ServerSettingsUpdateRequest,
    SpeechJobCreateResponse,
    SpeechRequest,
    StatsResponse,
    TaskType,
    TranscriptionResponse,
    VoiceProfileCreateResponse,
    VoiceProfileListItem,
)
from ..domain.state import ApiKeyRecord, VoiceProfileRecord, new_id, utcnow
from ..security import generate_api_key, get_admin_record, hash_key, require_admin_key, require_api_key, rotate_admin_key
from ..services import BenchmarkService, EventHub, QueueService, TranscriptionService

router = APIRouter()
protected = APIRouter(dependencies=[Depends(require_api_key)])
health = APIRouter()
admin = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin_key)])


def _supported_task_types(model_id: str) -> list[TaskType]:
    if model_id.endswith('VoiceDesign'):
        return [TaskType.voice_design]
    if model_id.endswith('CustomVoice'):
        return [TaskType.custom_voice]
    return [TaskType.base]


def _settings_response(settings: Any) -> ServerSettingsResponse:
    return ServerSettingsResponse(
        model_directory=str(settings.models_root_dir),
        default_model=settings.active_model,
        default_voice=settings.default_voice,
        whisper_base_url=settings.whisper_base_url,
        whisper_path=settings.whisper_path,
        retention_days=settings.retention_days,
        queue_limit=settings.max_queue_size,
        runtime_backend=settings.runtime_backend,
        allow_model_downloads=settings.allow_model_downloads,
        preferred_device=settings.preferred_device,
        attention_implementation=settings.attention_implementation,
        torch_dtype=settings.torch_dtype,
        sample_rate=settings.sample_rate,
        poll_interval_ms=settings.frontend_poll_interval_ms,
        theme=settings.frontend_theme,
        built_in_voices=settings.built_in_voices,
        sentence_chunking=settings.sentence_chunking,
        short_sentence_merge_max_chars=settings.short_sentence_merge_max_chars,
        following_sentence_merge_min_chars=settings.following_sentence_merge_min_chars,
        max_parallel_requests=settings.max_parallel_requests,
        max_batch_size=settings.max_batch_size,
        batch_wait_ms=settings.batch_wait_ms,
        stream_chunk_ms=settings.stream_chunk_ms,
        stream_prebuffer_ms=settings.stream_prebuffer_ms,
    )


async def _submit_or_429(queue: QueueService, payload: SpeechRequest):
    try:
        return await queue.submit(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


@health.get('/health')
async def healthcheck() -> dict[str, bool]:
    return {'ok': True}


@router.get('/api/health')
async def healthcheck_api() -> dict[str, bool]:
    return await healthcheck()


def _admin_key_metadata(store: Any) -> AdminKeyMetadata:
    record = get_admin_record(store)
    return AdminKeyMetadata(
        key_id=record.key_id,
        label='Master Admin Key',
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


@admin.get('/keys', response_model=AdminKeyResponse)
async def get_admin_keys(request: Request) -> AdminKeyResponse:
    return AdminKeyResponse(admin_key=_admin_key_metadata(request.app.state.store))


@admin.post('/keys', response_model=AdminKeyRotateResponse)
async def rotate_keys(request: Request) -> AdminKeyRotateResponse:
    record, token = rotate_admin_key(request.app.state.store, request.app.state.settings)
    return AdminKeyRotateResponse(
        admin_key=AdminKeyMetadata(
            key_id=record.key_id,
            label='Master Admin Key',
            created_at=record.created_at,
            last_used_at=record.last_used_at,
        ),
        token=token,
    )


@admin.get('/settings', response_model=ServerSettingsResponse)
async def get_admin_settings(request: Request) -> ServerSettingsResponse:
    return _settings_response(request.app.state.settings)


@admin.put('/settings', response_model=ServerSettingsResponse)
async def update_admin_settings(request: Request, payload: ServerSettingsUpdateRequest) -> ServerSettingsResponse:
    return await update_settings(request, payload)


@protected.get('/v1/settings', response_model=ServerSettingsResponse)
async def get_settings(request: Request) -> ServerSettingsResponse:
    return _settings_response(request.app.state.settings)


@protected.put('/v1/settings', response_model=ServerSettingsResponse)
async def update_settings(request: Request, payload: ServerSettingsUpdateRequest) -> ServerSettingsResponse:
    settings = request.app.state.settings
    store = request.app.state.store

    if payload.default_model is not None:
        if payload.default_model not in settings.supported_models:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported default model')
        settings.active_model = payload.default_model

    if payload.default_voice is not None:
        settings.default_voice = payload.default_voice

    if payload.model_directory is not None:
        settings.models_root_dir = Path(payload.model_directory).expanduser()
        settings.models_root_dir.mkdir(parents=True, exist_ok=True)

    if payload.whisper_base_url is not None:
        settings.whisper_base_url = payload.whisper_base_url or None
    if payload.whisper_path is not None:
        settings.whisper_path = payload.whisper_path
    if payload.retention_days is not None:
        settings.retention_days = payload.retention_days
    if payload.queue_limit is not None:
        settings.max_queue_size = payload.queue_limit
        store.max_queue_size = payload.queue_limit
    if payload.allow_model_downloads is not None:
        settings.allow_model_downloads = payload.allow_model_downloads
    if payload.preferred_device is not None:
        settings.preferred_device = payload.preferred_device
    if payload.attention_implementation is not None:
        settings.attention_implementation = payload.attention_implementation
    if payload.torch_dtype is not None:
        settings.torch_dtype = payload.torch_dtype
    if payload.poll_interval_ms is not None:
        settings.frontend_poll_interval_ms = payload.poll_interval_ms
    if payload.theme is not None:
        settings.frontend_theme = payload.theme
    if payload.sentence_chunking is not None:
        settings.sentence_chunking = payload.sentence_chunking
    if payload.short_sentence_merge_max_chars is not None:
        settings.short_sentence_merge_max_chars = payload.short_sentence_merge_max_chars
    if payload.following_sentence_merge_min_chars is not None:
        settings.following_sentence_merge_min_chars = payload.following_sentence_merge_min_chars
    if payload.max_parallel_requests is not None:
        settings.max_parallel_requests = payload.max_parallel_requests
    if payload.max_batch_size is not None:
        settings.max_batch_size = payload.max_batch_size
    if payload.batch_wait_ms is not None:
        settings.batch_wait_ms = payload.batch_wait_ms
    if payload.stream_chunk_ms is not None:
        settings.stream_chunk_ms = payload.stream_chunk_ms
    if payload.stream_prebuffer_ms is not None:
        settings.stream_prebuffer_ms = payload.stream_prebuffer_ms

    return _settings_response(settings)


@protected.post('/v1/audio/speech', response_model=None)
async def speech(request: Request, payload: SpeechRequest) -> Response | StreamingResponse:
    if not payload.input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='input is required')
    if payload.stream:
        if payload.response_format.lower() != 'pcm':
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='stream=true requires response_format=pcm')
        if payload.speed != 1.0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='stream=true currently requires speed=1.0')
    queue: QueueService = request.app.state.queue_service
    if payload.stream:
        job = await _submit_or_429(queue, payload)

        async def stream_job() -> Any:
            while True:
                chunk = await job.stream_chunks.get()
                if chunk is None:
                    break
                yield chunk

        return StreamingResponse(stream_job(), media_type='audio/pcm')

    job = await _submit_or_429(queue, payload)
    finished = await queue.wait_for_completion(job.job_id)
    if finished.status != JobStatus.completed or not finished.final_audio:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=finished.error_message or 'synthesis failed')
    return Response(content=finished.final_audio, media_type=finished.content_type or 'audio/wav')


@protected.post('/v1/jobs', response_model=SpeechJobCreateResponse)
async def create_job(request: Request, payload: SpeechRequest) -> SpeechJobCreateResponse:
    if not payload.input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='input is required')
    if payload.stream and payload.speed != 1.0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='stream=true currently requires speed=1.0')
    queue: QueueService = request.app.state.queue_service
    job = await _submit_or_429(queue, payload)
    return SpeechJobCreateResponse(job_id=job.job_id, status=job.status, queue_position=job.queue_position, eta_ms=job.eta_ms)


@protected.get('/v1/jobs', response_model=list[JobListItem])
async def list_jobs(request: Request) -> list[JobListItem]:
    queue: QueueService = request.app.state.queue_service
    return [
        JobListItem(
            job_id=job.job_id,
            status=job.status,
            model=job.model_used or job.request.model,
            task_type=job.request.task_type,
            voice=job.request.voice,
            input_preview=job.preview(),
            queue_position=job.queue_position,
            eta_ms=job.eta_ms,
            created_at=job.created_at,
            updated_at=job.updated_at,
            metrics=job.metrics,
            error_message=job.error_message,
        )
        for job in sorted(queue.store.jobs.values(), key=lambda item: item.created_at, reverse=True)
    ]


@protected.get('/v1/jobs/{job_id}', response_model=JobDetailResponse)
async def get_job(request: Request, job_id: str) -> JobDetailResponse:
    queue: QueueService = request.app.state.queue_service
    job = queue.store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    return JobDetailResponse(
        job_id=job.job_id,
        status=job.status,
        model=job.model_used or job.request.model,
        task_type=job.request.task_type,
        voice=job.request.voice,
        input_preview=job.preview(),
        queue_position=job.queue_position,
        eta_ms=job.eta_ms,
        created_at=job.created_at,
        updated_at=job.updated_at,
        metrics=job.metrics,
        error_message=job.error_message,
    )


@protected.delete('/v1/jobs/{job_id}')
async def delete_job(request: Request, job_id: str) -> dict[str, bool]:
    queue: QueueService = request.app.state.queue_service
    job = queue.store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    try:
        await queue.cancel(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {'ok': True}


@protected.get('/v1/jobs/{job_id}/audio')
async def get_job_audio(request: Request, job_id: str) -> Response:
    queue: QueueService = request.app.state.queue_service
    job = queue.store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    if not job.final_audio:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Audio not ready')
    return Response(content=job.final_audio, media_type=job.content_type or 'audio/wav')


@protected.get('/v1/stats', response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    return request.app.state.stats_service.build_stats(request.app.state.store)


@protected.get('/v1/stats/gpu', response_model=GpuStatsResponse)
async def gpu_stats(request: Request) -> GpuStatsResponse:
    return request.app.state.stats_service.build_gpu_stats()


@protected.get('/v1/audio/voices', response_model=list[VoiceProfileListItem])
async def list_voices(request: Request) -> list[VoiceProfileListItem]:
    store = request.app.state.store
    settings = request.app.state.settings
    built_in = [
        VoiceProfileListItem(voice_id=name.lower(), name=name, source='built-in', created_at=None)
        for name in settings.built_in_voices
    ]
    custom = [
        VoiceProfileListItem(voice_id=voice.voice_id, name=voice.name, source=voice.source, created_at=voice.created_at)
        for voice in store.voice_profiles.values()
    ]
    return built_in + custom


@protected.post('/v1/audio/voices', response_model=VoiceProfileCreateResponse)
async def create_voice_profile(
    request: Request,
    audio_sample: UploadFile = File(...),
    name: str = Form(...),
    consent: bool = Form(False),
    ref_text: str | None = Form(default=None),
) -> VoiceProfileCreateResponse:
    import io as _io
    raw = await audio_sample.read()
    # Normalize any browser audio format (webm, ogg, mp3, wav …) to a PCM WAV
    # so soundfile can reliably read it back during voice cloning.
    wav_bytes = await asyncio.to_thread(_transcode_to_wav, raw)
    store = request.app.state.store
    voice = VoiceProfileRecord(
        voice_id=new_id('voice'),
        name=name,
        source='custom',
        created_at=utcnow(),
        audio_bytes=wav_bytes,
        content_type='audio/wav',
        filename=(audio_sample.filename or 'sample') + '.normalized.wav',
        ref_text=ref_text,
        consent=consent,
    )
    store.voice_profiles[voice.voice_id] = voice
    store.save_voices(request.app.state.settings.data_dir)
    return VoiceProfileCreateResponse(voice_id=voice.voice_id, name=voice.name, source=voice.source, created_at=voice.created_at)


def _transcode_to_wav(raw: bytes) -> bytes:
    """Convert any audio bytes to a standard 24kHz mono WAV using soundfile/librosa."""
    import io as _io
    import soundfile as sf
    import numpy as np

    # First try soundfile (handles WAV, FLAC, OGG-Vorbis natively)
    buf = _io.BytesIO(raw)
    try:
        audio, sr = sf.read(buf, dtype='float32', always_2d=False)
    except Exception:
        # Fall back to librosa which handles MP3, WebM, OGG-Opus, etc. via ffmpeg/soundfile
        try:
            import librosa
            buf.seek(0)
            audio, sr = librosa.load(buf, sr=None, mono=True)
        except Exception as exc:
            raise RuntimeError(
                f'Could not decode the uploaded audio. '
                f'Supported formats: WAV, FLAC, OGG, MP3 (requires ffmpeg). Error: {exc}'
            ) from exc

    # Convert to mono if needed
    if audio.ndim > 1:
        audio = np.mean(audio, axis=-1)

    # Resample to 24kHz (model target sample rate)
    target_sr = 24_000
    if sr != target_sr:
        import librosa
        audio = librosa.resample(audio.astype(np.float32), orig_sr=int(sr), target_sr=target_sr)
        sr = target_sr

    out = _io.BytesIO()
    sf.write(out, audio.astype(np.float32), sr, format='WAV', subtype='PCM_16')
    return out.getvalue()


@protected.delete('/v1/audio/voices/{voice_id}')
async def delete_voice_profile(request: Request, voice_id: str) -> dict[str, bool]:
    store = request.app.state.store
    if voice_id not in store.voice_profiles:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Voice not found')
    store.voice_profiles.pop(voice_id, None)
    store.save_voices(request.app.state.settings.data_dir)
    return {'ok': True}


@protected.get('/v1/models', response_model=list[ModelInfo])
async def list_models(request: Request) -> list[ModelInfo]:
    settings = request.app.state.settings
    store = request.app.state.store
    active = store.active_model or settings.active_model
    return [
        ModelInfo(
            model_id=model,
            loaded=model in store.models_loaded or model == active,
            active=model == active,
            task_types=_supported_task_types(model),
        )
        for model in settings.supported_models
    ]


@protected.get('/v1/keys', response_model=list[ApiKeyListItem])
async def list_keys(request: Request) -> list[ApiKeyListItem]:
    store = request.app.state.store
    return [
        ApiKeyListItem(
            key_id=record.key_id,
            name=record.name,
            created_at=record.created_at,
            last_used_at=record.last_used_at,
            disabled=record.disabled,
        )
        for record in store.api_keys.values()
    ]


@protected.post('/v1/keys', response_model=ApiKeyCreateResponse)
async def create_key(request: Request, payload: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
    store = request.app.state.store
    raw_key = generate_api_key()
    record = ApiKeyRecord(
        key_id=new_id('key'),
        name=payload.name,
        key_hash=hash_key(raw_key),
        created_at=utcnow(),
    )
    store.api_keys[record.key_id] = record
    store.save_secrets(request.app.state.settings.data_dir)
    return ApiKeyCreateResponse(key_id=record.key_id, name=record.name, api_key=raw_key, created_at=record.created_at)


@protected.delete('/v1/keys/{key_id}')
async def delete_key(request: Request, key_id: str) -> dict[str, bool]:
    store = request.app.state.store
    record = store.api_keys.pop(key_id, None)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Key not found')
    store.save_secrets(request.app.state.settings.data_dir)
    return {'ok': True}


@protected.post('/v1/tools/transcribe', response_model=TranscriptionResponse)
async def transcribe(request: Request, file: UploadFile = File(...)) -> TranscriptionResponse:
    data = await file.read()
    service: TranscriptionService = request.app.state.transcription_service
    return await service.transcribe(file.filename or 'audio.wav', file.content_type or 'audio/wav', data)


@protected.post('/v1/benchmarks/runs', response_model=BenchmarkRunResponse)
async def create_benchmark(request: Request, payload: BenchmarkRunCreateRequest) -> BenchmarkRunResponse:
    service: BenchmarkService = request.app.state.benchmark_service
    return await service.create_run(payload)


@protected.get('/v1/benchmarks/runs', response_model=list[BenchmarkRunResponse])
async def list_benchmarks(request: Request) -> list[BenchmarkRunResponse]:
    service: BenchmarkService = request.app.state.benchmark_service
    return await service.list_runs()


@protected.get('/v1/benchmarks/runs/{run_id}', response_model=BenchmarkRunResponse)
async def get_benchmark(request: Request, run_id: str) -> BenchmarkRunResponse:
    service: BenchmarkService = request.app.state.benchmark_service
    try:
        return service.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Run not found') from exc


@protected.delete('/v1/benchmarks/runs/{run_id}')
async def delete_benchmark(request: Request, run_id: str) -> dict[str, bool]:
    service: BenchmarkService = request.app.state.benchmark_service
    await service.delete_run(run_id)
    return {'ok': True}


@protected.get('/v1/events')
async def event_stream(request: Request) -> StreamingResponse:
    events: EventHub = request.app.state.events
    queue = await events.subscribe()

    async def iterator() -> Any:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ': ping\n\n'
                    continue
                if message is None:
                    break
                yield EventHub.encode_sse(message['event'], message['data'])
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(iterator(), media_type='text/event-stream')


router.include_router(health)
router.include_router(admin)
router.include_router(protected)
