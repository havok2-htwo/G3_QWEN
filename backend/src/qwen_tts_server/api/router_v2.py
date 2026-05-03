from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ..config import save_runtime_settings
from ..domain.models import (
    AdminKeyMetadata,
    AdminKeyResponse,
    AdminKeyRotateResponse,
    BatchSnapshot,
    DashboardOverview,
    DashboardSnapshot,
    GpuStatsResponse,
    JobDetailResponse,
    JobListItem,
    JobStatus,
    ModelInfo,
    ServerSettingsResponse,
    ServerSettingsUpdateRequest,
    SpeechJobCreateResponse,
    SpeechRequest,
    SynthesisResultResponse,
    TaskType,
    TranscriptionResponse,
    VoiceCatalogResponse,
    VoiceProfileCreateResponse,
    VoiceProfileListItem,
)
from ..domain.state import VoiceProfileRecord, new_id, utcnow
from ..security import get_admin_record, require_admin_key, rotate_admin_key
from ..services_v2 import EventHub, QueueService, TranscriptionService

router = APIRouter()
health = APIRouter()
admin = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin_key)])


def _supported_task_types(model_id: str) -> list[TaskType]:
    if model_id.endswith('VoiceDesign'):
        return [TaskType.voice_design]
    if model_id.endswith('Base'):
        return [TaskType.base]
    return [TaskType.custom_voice]


def _effective_task_type(model_id: str | None, request_task_type: TaskType | None) -> TaskType:
    if request_task_type is not None:
        return request_task_type
    target_model = model_id or ''
    if target_model.endswith('VoiceDesign'):
        return TaskType.voice_design
    if target_model.endswith('Base'):
        return TaskType.base
    return TaskType.custom_voice


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


def _voice_items(request: Request) -> list[VoiceProfileListItem]:
    store = request.app.state.store
    settings = request.app.state.settings
    built_in = [
        VoiceProfileListItem(voice_id=name.lower(), name=name, source='built-in', created_at=None)
        for name in settings.built_in_voices
    ]
    custom = [
        VoiceProfileListItem(voice_id=voice.voice_id, name=voice.name, source=voice.source, created_at=voice.created_at)
        for voice in sorted(store.voice_profiles.values(), key=lambda item: item.created_at, reverse=True)
    ]
    return built_in + custom


def _model_items(request: Request) -> list[ModelInfo]:
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


def _job_item(request: Request, job_id: str) -> JobListItem:
    job = request.app.state.store.jobs[job_id]
    return JobListItem(
        job_id=job.job_id,
        status=job.status,
        model=job.model_used or job.request.model,
        task_type=_effective_task_type(job.model_used or job.request.model, job.request.task_type),
        voice=job.request.voice,
        input_preview=job.preview(),
        queue_position=job.queue_position,
        eta_ms=job.eta_ms,
        created_at=job.created_at,
        updated_at=job.updated_at,
        metrics=job.metrics,
        error_message=job.error_message,
    )


def _job_detail(request: Request, job_id: str) -> JobDetailResponse:
    job = request.app.state.store.jobs[job_id]
    return JobDetailResponse(
        job_id=job.job_id,
        status=job.status,
        model=job.model_used or job.request.model,
        task_type=_effective_task_type(job.model_used or job.request.model, job.request.task_type),
        voice=job.request.voice,
        input_preview=job.preview(),
        queue_position=job.queue_position,
        eta_ms=job.eta_ms,
        created_at=job.created_at,
        updated_at=job.updated_at,
        metrics=job.metrics,
        error_message=job.error_message,
        started_at=job.started_at,
        first_audio_at=job.first_audio_at,
        completed_at=job.completed_at,
        sentences_total=job.sentences_total,
        batch_count=int(job.metrics.get('batch_count') or 0),
    )


def _admin_key_metadata(request: Request) -> AdminKeyMetadata:
    record = get_admin_record(request.app.state.store)
    return AdminKeyMetadata(
        key_id=record.key_id,
        label='Master Admin Key',
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


def _dashboard_snapshot(request: Request) -> DashboardSnapshot:
    store = request.app.state.store
    stats = request.app.state.stats_service.build_stats(store)
    gpu: GpuStatsResponse = request.app.state.stats_service.build_gpu_stats()
    current_batch = None
    if store.current_batch:
        current_batch = BatchSnapshot(
            batch_id=store.current_batch['batch_id'],
            model_id=store.current_batch['model_id'],
            task_type=TaskType(store.current_batch['task_type']),
            voice=store.current_batch.get('voice'),
            language=store.current_batch.get('language'),
            size=store.current_batch['size'],
            started_at=store.current_batch['started_at'],
            request_ids=list(store.current_batch.get('request_ids', [])),
            sentence_indices=list(store.current_batch.get('sentence_indices', [])),
        )

    recent_batches = [
        BatchSnapshot(
            batch_id=item['batch_id'],
            model_id=item['model_id'],
            task_type=TaskType(item['task_type']),
            voice=item.get('voice'),
            language=item.get('language'),
            size=item['size'],
            started_at=item['started_at'],
            request_ids=list(item.get('request_ids', [])),
            sentence_indices=list(item.get('sentence_indices', [])),
        )
        for item in list(store.recent_batches)[-10:]
    ]

    overview = DashboardOverview(
        active_model=stats.active_model,
        queue_depth=stats.queue_depth,
        active_requests=store.active_requests(),
        worker_state=stats.worker_state,
        ttfa_ms_avg=stats.rolling.ttfa_ms_avg,
        queue_wait_ms_avg=stats.rolling.queue_wait_ms_avg,
        job_wall_ms_avg=stats.rolling.job_wall_ms_avg,
        realtime_x_avg=stats.rolling.realtime_x_avg,
        jobs_total=stats.global_.jobs_total,
        audio_seconds_total=stats.global_.audio_seconds_total,
        gpu_name=gpu.name,
        gpu_memory_used_mb=gpu.memory_used_mb,
        gpu_memory_total_mb=gpu.memory_total_mb,
        gpu_utilization_pct=gpu.utilization_percent,
        gpu_temperature_c=gpu.temperature_c,
    )
    jobs = [_job_item(request, job_id) for job_id in sorted(store.jobs, key=lambda value: store.jobs[value].created_at, reverse=True)[:24]]
    return DashboardSnapshot(
        overview=overview,
        settings=_settings_response(request.app.state.settings),
        models=_model_items(request),
        voices=_voice_items(request),
        jobs=jobs,
        admin_key=_admin_key_metadata(request),
        current_batch=current_batch,
        recent_batches=recent_batches,
    )


async def _submit_or_429(queue: QueueService, payload: SpeechRequest, *, owner_scope: str = 'public'):
    try:
        return await queue.submit(payload, owner_scope=owner_scope)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


@health.get('/health')
async def healthcheck() -> dict[str, bool]:
    return {'ok': True}


@router.get('/api/health')
async def healthcheck_api() -> dict[str, bool]:
    return await healthcheck()


@admin.get('/keys', response_model=AdminKeyResponse)
async def get_admin_keys(request: Request) -> AdminKeyResponse:
    return AdminKeyResponse(admin_key=_admin_key_metadata(request))


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

    save_runtime_settings(settings)
    return _settings_response(settings)


@admin.get('/snapshot', response_model=DashboardSnapshot)
async def admin_snapshot(request: Request) -> DashboardSnapshot:
    return _dashboard_snapshot(request)


@admin.get('/dashboard/stream')
async def admin_dashboard_stream(request: Request) -> StreamingResponse:
    events: EventHub = request.app.state.events
    queue = await events.subscribe()

    async def iterator() -> Any:
        try:
            yield EventHub.encode_sse('dashboard.snapshot', _dashboard_snapshot(request).model_dump(mode='json'))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ': ping\n\n'
                    continue
                yield EventHub.encode_sse('dashboard.snapshot', _dashboard_snapshot(request).model_dump(mode='json'))
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(iterator(), media_type='text/event-stream')


@admin.get('/jobs', response_model=list[JobListItem])
async def admin_jobs(request: Request) -> list[JobListItem]:
    return [
        _job_item(request, job_id)
        for job_id in sorted(request.app.state.store.jobs, key=lambda value: request.app.state.store.jobs[value].created_at, reverse=True)
    ]


@admin.get('/jobs/{job_id}', response_model=JobDetailResponse)
async def admin_job_detail(request: Request, job_id: str) -> JobDetailResponse:
    if job_id not in request.app.state.store.jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    return _job_detail(request, job_id)


@admin.get('/jobs/{job_id}/audio')
async def admin_job_audio(request: Request, job_id: str) -> Response:
    job = request.app.state.store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    if not job.final_audio:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Audio not ready')
    return Response(content=job.final_audio, media_type=job.content_type or 'audio/wav')


@admin.delete('/jobs/{job_id}')
async def admin_delete_job(request: Request, job_id: str) -> dict[str, bool]:
    queue: QueueService = request.app.state.queue_service
    if job_id not in request.app.state.store.jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    await queue.delete(job_id)
    return {'ok': True}


@admin.get('/voices', response_model=list[VoiceProfileListItem])
async def admin_list_voices(request: Request) -> list[VoiceProfileListItem]:
    return _voice_items(request)


@admin.post('/voices', response_model=VoiceProfileCreateResponse)
async def create_voice_profile(
    request: Request,
    audio_sample: UploadFile = File(...),
    name: str = Form(...),
    consent: bool = Form(False),
    ref_text: str | None = Form(default=None),
) -> VoiceProfileCreateResponse:
    raw = await audio_sample.read()
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
    store.prompt_cache.clear()
    store.save_voices(request.app.state.settings.data_dir)
    await request.app.state.events.publish('dashboard.snapshot', {'reason': 'voice.created'})
    return VoiceProfileCreateResponse(voice_id=voice.voice_id, name=voice.name, source=voice.source, created_at=voice.created_at)


def _transcode_to_wav(raw: bytes) -> bytes:
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO(raw)
    try:
        audio, sr = sf.read(buf, dtype='float32', always_2d=False)
    except Exception:
        try:
            import librosa

            buf.seek(0)
            audio, sr = librosa.load(buf, sr=None, mono=True)
        except Exception as exc:
            raise RuntimeError(f'Could not decode the uploaded audio: {exc}') from exc

    if audio.ndim > 1:
        audio = np.mean(audio, axis=-1)

    target_sr = 24_000
    if sr != target_sr:
        try:
            import librosa

            audio = librosa.resample(audio.astype(np.float32), orig_sr=int(sr), target_sr=target_sr)
            sr = target_sr
        except Exception as exc:
            raise RuntimeError(f'Could not resample the uploaded audio: {exc}') from exc

    out = io.BytesIO()
    sf.write(out, audio.astype(np.float32), sr, format='WAV', subtype='PCM_16')
    return out.getvalue()


@admin.delete('/voices/{voice_id}')
async def delete_voice_profile(request: Request, voice_id: str) -> dict[str, bool]:
    store = request.app.state.store
    if voice_id not in store.voice_profiles:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Voice not found')
    store.voice_profiles.pop(voice_id, None)
    store.prompt_cache.clear()
    store.save_voices(request.app.state.settings.data_dir)
    await request.app.state.events.publish('dashboard.snapshot', {'reason': 'voice.deleted'})
    return {'ok': True}


@admin.post('/voices/transcribe', response_model=TranscriptionResponse)
async def transcribe(request: Request, file: UploadFile = File(...)) -> TranscriptionResponse:
    data = await file.read()
    service: TranscriptionService = request.app.state.transcription_service
    return await service.transcribe(file.filename or 'audio.wav', file.content_type or 'audio/wav', data)


@router.get('/api/v1/voices', response_model=VoiceCatalogResponse)
async def list_public_voices(request: Request) -> VoiceCatalogResponse:
    return VoiceCatalogResponse(voices=_voice_items(request))


@router.get('/v1/voices', response_model=list[VoiceProfileListItem])
async def list_voices_alias(request: Request) -> list[VoiceProfileListItem]:
    return _voice_items(request)


@router.get('/v1/audio/voices', response_model=list[VoiceProfileListItem])
async def list_audio_voices(request: Request) -> list[VoiceProfileListItem]:
    return _voice_items(request)


@router.get('/v1/models', response_model=list[ModelInfo])
async def list_models(request: Request) -> list[ModelInfo]:
    return _model_items(request)


@router.post('/api/v1/synthesize', response_model=SynthesisResultResponse)
async def synthesize(request: Request, payload: SpeechRequest) -> SynthesisResultResponse:
    queue: QueueService = request.app.state.queue_service
    job = await _submit_or_429(queue, payload.model_copy(update={'stream': False}), owner_scope='public')
    finished = await queue.wait_for_completion(job.job_id)
    if finished.status == JobStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=finished.error_message or 'Synthesis cancelled')
    if finished.status != JobStatus.completed:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=finished.error_message or 'Synthesis failed')
    return SynthesisResultResponse(
        job_id=finished.job_id,
        status=finished.status,
        model=finished.model_used or finished.request.model,
        task_type=_effective_task_type(finished.model_used or finished.request.model, finished.request.task_type),
        voice=finished.request.voice,
        sample_rate=finished.sample_rate,
        metrics=finished.metrics,
    )


@router.post('/api/v1/synthesize/stream')
async def synthesize_stream(request: Request, payload: SpeechRequest) -> StreamingResponse:
    queue: QueueService = request.app.state.queue_service
    job = await _submit_or_429(queue, payload.model_copy(update={'stream': True, 'response_format': 'pcm'}), owner_scope='public')

    async def iterator() -> Any:
        while True:
            event = await job.stream_events.get()
            if event is None:
                break
            yield json.dumps(event) + '\n'

    return StreamingResponse(iterator(), media_type='application/x-ndjson')


@router.post('/v1/audio/speech', response_model=None)
async def speech(request: Request, payload: SpeechRequest) -> Response | StreamingResponse:
    queue: QueueService = request.app.state.queue_service
    if payload.stream:
        job = await _submit_or_429(queue, payload.model_copy(update={'stream': True, 'response_format': 'pcm'}), owner_scope='public')

        async def stream_job() -> Any:
            while True:
                chunk = await job.stream_chunks.get()
                if chunk is None:
                    break
                yield chunk

        return StreamingResponse(stream_job(), media_type='audio/pcm')

    job = await _submit_or_429(queue, payload.model_copy(update={'stream': False}), owner_scope='public')
    finished = await queue.wait_for_completion(job.job_id)
    if finished.status == JobStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=finished.error_message or 'Synthesis cancelled')
    if finished.status != JobStatus.completed or not finished.final_audio:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=finished.error_message or 'Synthesis failed')
    return Response(content=finished.final_audio, media_type=finished.content_type or 'audio/wav')


@router.post('/v1/jobs', response_model=SpeechJobCreateResponse)
async def create_job(request: Request, payload: SpeechRequest) -> SpeechJobCreateResponse:
    queue: QueueService = request.app.state.queue_service
    job = await _submit_or_429(queue, payload, owner_scope='public')
    return SpeechJobCreateResponse(job_id=job.job_id, status=job.status, queue_position=job.queue_position, eta_ms=job.eta_ms)


router.include_router(health)
router.include_router(admin)
