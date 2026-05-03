from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import statistics
import struct
import wave

from collections import defaultdict, deque
from typing import Any

import httpx

from .config import Settings
from .domain.models import (
    GpuStatsResponse,
    JobStatus,
    SpeechRequest,
    StatGlobal,
    StatRolling,
    StatsResponse,
    TranscriptionResponse,
)
from .domain.state import (
    InMemoryStore,
    JobRecord,
    utcnow,
)
from .runtime import query_nvidia_smi

class EventHub:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        message = {'event': event, 'data': payload}
        stale: list[asyncio.Queue[dict[str, Any] | None]] = []
        for queue in list(self.store.event_subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.store.event_subscribers.discard(queue)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self.store.event_subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self.store.event_subscribers.discard(queue)

    @staticmethod
    def encode_sse(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


class MockSynthesizer:
    def __init__(self, sample_rate: int = 24_000) -> None:
        self.sample_rate = sample_rate

    async def ensure_model(self, requested_model: str | None) -> tuple[str, int]:
        return requested_model or 'mock-model', 0

    def duration_ms(self, text: str) -> int:
        return max(650, min(4500, 260 + len(text) * 34))

    def frequency_for(self, text: str) -> int:
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        return 170 + digest[0] % 220

    def pcm_for(self, request: SpeechRequest) -> bytes:
        text = request.input or ''
        duration_ms = self.duration_ms(text)
        total_samples = int(self.sample_rate * duration_ms / 1000)
        freq = self.frequency_for(text)
        amplitude = 10_000
        frames = bytearray()
        for sample_index in range(total_samples):
            value = int(amplitude * math.sin(2 * math.pi * freq * sample_index / self.sample_rate))
            frames += struct.pack('<h', value)
        return bytes(frames)

    def pcm_to_wav(self, pcm: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    async def stream_pcm(self, request: SpeechRequest, chunk_ms: int = 120):
        text = request.input or ''
        duration_ms = self.duration_ms(text)
        total_samples = int(self.sample_rate * duration_ms / 1000)
        freq = self.frequency_for(text)
        amplitude = 10_000
        chunk_samples = max(1, int(self.sample_rate * chunk_ms / 1000))
        produced = 0
        while produced < total_samples:
            frame_count = min(chunk_samples, total_samples - produced)
            chunk = bytearray()
            for offset in range(frame_count):
                sample_index = produced + offset
                value = int(amplitude * math.sin(2 * math.pi * freq * sample_index / self.sample_rate))
                chunk += struct.pack('<h', value)
            produced += frame_count
            if produced == frame_count:
                await asyncio.sleep(0.02)
            else:
                await asyncio.sleep(0.01)
            yield bytes(chunk)

    async def render_wav(self, request: SpeechRequest) -> tuple[bytes, int]:
        text = request.input or ''
        pcm = self.pcm_for(request)
        return self.pcm_to_wav(pcm), self.duration_ms(text)


class QueueService:
    def __init__(self, store: InMemoryStore, synthesizer: Any, events: EventHub) -> None:
        self.store = store
        self.synthesizer = synthesizer
        self.events = events

    async def start_worker(self) -> None:
        if self.store.worker_task and not self.store.worker_task.done():
            return
        self.store.worker_stop.clear()
        self.store.worker_task = asyncio.create_task(self._worker_loop())

    async def stop_worker(self) -> None:
        self.store.worker_stop.set()
        async with self.store.job_condition:
            self.store.job_condition.notify_all()
        if self.store.worker_task:
            try:
                await self.store.worker_task
            except Exception:
                pass

    async def submit(self, request: SpeechRequest) -> JobRecord:
        if len(self.store.job_queue) >= self.store.max_queue_size:
            raise RuntimeError('Queue saturated')
        now = utcnow()
        job = JobRecord(job_id=f'job_{now.timestamp():.0f}_{len(self.store.jobs):04d}', request=request, created_at=now, updated_at=now)
        self.store.jobs[job.job_id] = job
        self.store.job_queue.append(job.job_id)
        self._recompute_positions()
        await self.events.publish('queue.updated', self.queue_snapshot())
        async with self.store.job_condition:
            self.store.job_condition.notify_all()
        return job

    async def cancel(self, job_id: str) -> JobRecord:
        job = self.store.jobs[job_id]
        if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
            raise RuntimeError('Job already finished')
        if job.status == JobStatus.cancelling:
            return job

        job.cancel_requested = True
        job.updated_at = utcnow()

        if job.status == JobStatus.queued:
            try:
                self.store.job_queue.remove(job_id)
            except ValueError:
                pass
            self._recompute_positions()
            return await self._mark_cancelled(job, message='Cancelled before execution.')

        job.status = JobStatus.cancelling
        job.error_message = 'Cancellation requested.'
        await self.events.publish('job.updated', self.job_snapshot(job))
        await self.events.publish('queue.updated', self.queue_snapshot())
        return job

    async def wait_for_completion(self, job_id: str) -> JobRecord:
        while True:
            job = self.store.jobs[job_id]
            if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
                return job
            await asyncio.sleep(0.01)

    def get(self, job_id: str) -> JobRecord:
        return self.store.jobs[job_id]

    def queue_snapshot(self) -> dict[str, Any]:
        return {
            'queue_depth': self.store.queue_depth(),
            'active_model': self.store.active_model,
            'worker_state': self.store.worker_state,
        }

    async def direct_render(self, request: SpeechRequest) -> tuple[bytes, dict[str, float]]:
        async with self.store.exclusive_lock:
            return await self._render_request(request)

    async def render_locked(self, request: SpeechRequest) -> tuple[bytes, dict[str, float]]:
        return await self._render_request(request)

    async def _worker_loop(self) -> None:
        self.store.worker_state = 'running'
        await self.events.publish('stats.updated', self.queue_snapshot())
        while not self.store.worker_stop.is_set():
            async with self.store.job_condition:
                await self.store.job_condition.wait_for(lambda: bool(self.store.job_queue) or self.store.worker_stop.is_set())
                if self.store.worker_stop.is_set():
                    break
                job_id = self.store.job_queue.popleft()
                self._recompute_positions()
            job = self.store.jobs[job_id]
            if job.status in {JobStatus.cancelled, JobStatus.cancelling}:
                await self.events.publish('queue.updated', self.queue_snapshot())
                continue
            try:
                await self._process_job(job)
            except Exception as exc:  # pragma: no cover - defensive
                if self._cancellation_requested(job):
                    await self._mark_cancelled(job, message='Cancelled during synthesis.')
                    continue
                job.status = JobStatus.failed
                job.error_message = str(exc)
                job.updated_at = utcnow()
                if job.request.stream:
                    await job.stream_chunks.put(None)
                await self.events.publish('job.updated', self.job_snapshot(job))
        self.store.worker_state = 'stopped'
        await self.events.publish('stats.updated', self.queue_snapshot())

    async def _process_job(self, job: JobRecord) -> None:
        text = job.request.input or ''
        if not text.strip():
            job.status = JobStatus.failed
            job.error_message = 'Missing input text'
            job.updated_at = utcnow()
            await self.events.publish('job.updated', self.job_snapshot(job))
            return

        if self._cancellation_requested(job):
            await self._mark_cancelled(job, message='Cancelled before execution.')
            return

        job.started_at = utcnow()
        job.updated_at = job.started_at
        target_model = job.request.model or self.store.active_model
        if target_model and target_model != self.store.active_model:
            job.status = JobStatus.warming
        else:
            job.status = JobStatus.running
        job.metrics['queue_wait_ms'] = int((job.started_at - job.created_at).total_seconds() * 1000)
        await self.events.publish('job.updated', self.job_snapshot(job))

        async with self.store.exclusive_lock:
            if self._cancellation_requested(job):
                await self._mark_cancelled(job, message='Cancelled before synthesis started.')
                return

            model_used, warm_ms = await self._ensure_model_ready(job.request)
            job.model_used = model_used
            job.metrics['model_warm_ms'] = warm_ms
            job.updated_at = utcnow()

            if self._cancellation_requested(job):
                await self._mark_cancelled(job, message='Cancelled during model warmup.')
                return

            if job.request.stream:
                job.status = JobStatus.streaming
                await self.events.publish('job.updated', self.job_snapshot(job))
                pcm = bytearray()
                first_audio = True
                async for chunk in self.synthesizer.stream_pcm(job.request):
                    if self._cancellation_requested(job):
                        await self._mark_cancelled(job, message='Cancelled during streaming.')
                        return
                    if first_audio:
                        job.first_audio_at = utcnow()
                        job.metrics['ttfa_ms'] = int((job.first_audio_at - job.started_at).total_seconds() * 1000)
                        first_audio = False
                    pcm.extend(chunk)
                    await job.stream_chunks.put(chunk)
                if self._cancellation_requested(job):
                    await self._mark_cancelled(job, message='Cancelled during streaming.')
                    return
                await job.stream_chunks.put(None)
                job.final_audio = self.synthesizer.pcm_to_wav(bytes(pcm))
                job.content_type = 'audio/wav'
                job.metrics['audio_duration_ms'] = int(len(pcm) / 2 / max(self.synthesizer.sample_rate, 1) * 1000)
            else:
                job.status = JobStatus.running
                job.updated_at = utcnow()
                await self.events.publish('job.updated', self.job_snapshot(job))
                audio_bytes, duration_ms = await self.synthesizer.render_wav(job.request)
                if self._cancellation_requested(job):
                    await self._mark_cancelled(job, message='Cancelled during synthesis.')
                    return
                job.first_audio_at = utcnow()
                job.metrics['ttfa_ms'] = int((job.first_audio_at - job.started_at).total_seconds() * 1000)
                job.final_audio = audio_bytes
                job.content_type = 'audio/wav'
                job.metrics['audio_duration_ms'] = duration_ms

        if self._cancellation_requested(job):
            await self._mark_cancelled(job, message='Cancelled during synthesis.')
            return

        job.completed_at = utcnow()
        job.status = JobStatus.completed
        job.updated_at = job.completed_at
        job.error_message = None
        job.metrics['job_wall_ms'] = int((job.completed_at - job.started_at).total_seconds() * 1000)
        job.metrics['output_bytes'] = len(job.final_audio or b'')
        wall_ms = max(job.metrics['job_wall_ms'] or 1, 1)
        duration_ms = int(job.metrics.get('audio_duration_ms') or self.synthesizer.duration_ms(text))
        job.metrics['realtime_x'] = round(duration_ms / wall_ms, 3)
        self.store.total_jobs_completed += 1
        self.store.total_audio_seconds += duration_ms / 1000
        self.store.completed_job_metrics.append(job.metrics.copy())
        await self.events.publish('job.updated', self.job_snapshot(job))
        await self.events.publish('stats.updated', self.queue_snapshot())

    async def _render_request(self, request: SpeechRequest) -> tuple[bytes, dict[str, float]]:
        start = utcnow()
        model_used, warm_ms = await self._ensure_model_ready(request)
        audio_bytes, duration_ms = await self.synthesizer.render_wav(request)
        finish = utcnow()
        ttfa_ms = int((finish - start).total_seconds() * 1000)
        metrics = {
            'model_warm_ms': warm_ms,
            'ttfa_ms': ttfa_ms,
            'job_wall_ms': ttfa_ms,
            'audio_duration_ms': duration_ms,
            'realtime_x': round(duration_ms / max(ttfa_ms, 1), 3),
        }
        if model_used:
            self.store.models_loaded.add(model_used)
            self.store.active_model = model_used
        return audio_bytes, metrics

    async def _ensure_model_ready(self, request: SpeechRequest) -> tuple[str, int]:
        requested_model = request.model or self.store.active_model
        model_used, warm_ms = await self.synthesizer.ensure_model(requested_model)
        if model_used:
            self.store.models_loaded.add(model_used)
            self.store.active_model = model_used
        return model_used, warm_ms

    def _cancellation_requested(self, job: JobRecord) -> bool:
        return job.cancel_requested or job.status in {JobStatus.cancelling, JobStatus.cancelled}

    async def _mark_cancelled(self, job: JobRecord, message: str = 'Cancelled by user.') -> JobRecord:
        if job.status == JobStatus.cancelled:
            return job

        now = utcnow()
        job.cancel_requested = True
        job.status = JobStatus.cancelled
        job.completed_at = now
        job.updated_at = now
        job.queue_position = 0
        job.eta_ms = 0
        job.error_message = message
        job.final_audio = None
        job.content_type = None
        job.metrics['output_bytes'] = 0
        if job.started_at and job.metrics.get('job_wall_ms') is None:
            job.metrics['job_wall_ms'] = int((now - job.started_at).total_seconds() * 1000)
        if job.request.stream:
            await job.stream_chunks.put(None)
        await self.events.publish('job.updated', self.job_snapshot(job))
        await self.events.publish('queue.updated', self.queue_snapshot())
        await self.events.publish('stats.updated', self.queue_snapshot())
        return job

    def _recompute_positions(self) -> None:
        for index, job_id in enumerate(self.store.job_queue, start=1):
            job = self.store.jobs[job_id]
            job.queue_position = index
            job.eta_ms = self.store.estimate_eta_ms(index, len(job.request.input or ''))
            job.updated_at = utcnow()

    @staticmethod
    def job_snapshot(job: JobRecord) -> dict[str, Any]:
        return {
            'job_id': job.job_id,
            'status': job.status.value if hasattr(job.status, 'value') else job.status,
            'queue_position': job.queue_position,
            'eta_ms': job.eta_ms,
            'model': job.model_used or job.request.model,
        }

class StatsService:
    @staticmethod
    def _avg(values: list[float]) -> float | None:
        return statistics.mean(values) if values else None

    def build_stats(self, store: InMemoryStore) -> StatsResponse:
        rolling = StatRolling()
        metrics = list(store.completed_job_metrics)
        if metrics:
            ttfa = [metric['ttfa_ms'] for metric in metrics if metric.get('ttfa_ms') is not None]
            queue_wait = [metric['queue_wait_ms'] for metric in metrics if metric.get('queue_wait_ms') is not None]
            job_wall = [metric['job_wall_ms'] for metric in metrics if metric.get('job_wall_ms') is not None]
            realtime = [metric['realtime_x'] for metric in metrics if metric.get('realtime_x') is not None]
            rolling.ttfa_ms_avg = self._avg(ttfa)
            rolling.queue_wait_ms_avg = self._avg(queue_wait)
            rolling.job_wall_ms_avg = self._avg(job_wall)
            rolling.realtime_x_avg = self._avg(realtime)

        global_stats = StatGlobal(
            jobs_total=store.total_jobs_completed,
            audio_seconds_total=round(store.total_audio_seconds, 3),
            realtime_x_avg=rolling.realtime_x_avg,
        )
        return StatsResponse(
            active_model=store.active_model or '',
            queue_depth=store.queue_depth(),
            worker_state=store.worker_state,
            rolling=rolling,
            global_=global_stats,
        )

    @staticmethod
    def build_gpu_stats() -> GpuStatsResponse:
        return GpuStatsResponse(**query_nvidia_smi())


class TranscriptionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, filename: str, content_type: str, data: bytes) -> TranscriptionResponse:
        if self.settings.whisper_base_url:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.settings.whisper_base_url.rstrip('/')}{self.settings.whisper_path}",
                    files={'file': (filename, data, content_type or 'audio/wav')},
                    data={'engine': 'lokal', 'voice_ident': 'true'},
                )
                response.raise_for_status()
                payload = response.json()
                transcription = payload.get('transcription') or payload.get('text') or ''
                return TranscriptionResponse(transcription=transcription, voice_vector=payload.get('voice_vector'))

        return TranscriptionResponse(
            transcription=f"Mock transcription for {filename or 'audio'} ({len(data)} bytes).",
            voice_vector=[0.1, -0.2, 0.3],
        )


class BenchmarkService:
    def __init__(self, store: InMemoryStore, queue: QueueService, events: EventHub) -> None:
        self.store = store
        self.queue = queue
        self.events = events

    async def create_run(self, payload: BenchmarkRunCreateRequest) -> BenchmarkRunResponse:
        run_id = f'bench_{len(self.store.benchmark_runs):04d}_{int(utcnow().timestamp())}'
        texts = payload.texts or [self._dataset_text(payload.dataset)]
        run = BenchmarkRunRecord(
            run_id=run_id,
            name=payload.name,
            dataset=payload.dataset,
            texts=texts,
            iterations=payload.iterations,
            warmup_iterations=payload.warmup_iterations,
            cooldown_ms=payload.cooldown_ms,
            exclusive=payload.exclusive,
            cases=[BenchmarkCaseRecord(label=case.label, request=case.request) for case in payload.cases],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.store.benchmark_runs[run_id] = run
        asyncio.create_task(self._execute_run(run))
        return self.to_response(run)

    async def list_runs(self) -> list[BenchmarkRunResponse]:
        return [self.to_response(run) for run in sorted(self.store.benchmark_runs.values(), key=lambda item: item.created_at, reverse=True)]

    def get_run(self, run_id: str) -> BenchmarkRunResponse:
        return self.to_response(self.store.benchmark_runs[run_id])

    async def delete_run(self, run_id: str) -> None:
        self.store.benchmark_runs.pop(run_id, None)

    async def _execute_run(self, run: BenchmarkRunRecord) -> None:
        run.status = 'running'
        run.updated_at = utcnow()
        await self.events.publish('benchmark.updated', {'run_id': run.run_id, 'status': run.status})
        try:
            if run.exclusive:
                async with self.store.exclusive_lock:
                    await self._execute_cases(run, exclusive=True)
            else:
                await self._execute_cases(run, exclusive=False)
            run.status = 'completed'
        except Exception as exc:  # pragma: no cover - defensive
            run.status = 'failed'
            run.raw['error'] = str(exc)
        finally:
            run.updated_at = utcnow()
            await self.events.publish('benchmark.updated', {'run_id': run.run_id, 'status': run.status})

    async def _execute_cases(self, run: BenchmarkRunRecord, exclusive: bool) -> None:
        summary_values: dict[str, list[float]] = defaultdict(list)
        for case in run.cases:
            case.iterations.clear()
            for round_index in range(run.warmup_iterations + run.iterations):
                text = run.texts[round_index % len(run.texts)]
                request = case.request.model_copy(update={'input': text})
                try:
                    if exclusive:
                        audio_bytes, metrics = await self.queue.render_locked(request)
                        queue_wait_ms = 0
                    else:
                        job = await self.queue.submit(request)
                        await self.queue.wait_for_completion(job.job_id)
                        result = self.queue.get(job.job_id)
                        audio_bytes = result.final_audio or b''
                        metrics = result.metrics
                        queue_wait_ms = int(metrics.get('queue_wait_ms') or 0)
                    record = BenchmarkIterationRecord(
                        iteration=round_index + 1,
                        text=text,
                        queue_wait_ms=queue_wait_ms,
                        model_warm_ms=int(metrics.get('model_warm_ms') or 0),
                        ttfa_ms=int(metrics.get('ttfa_ms') or 0),
                        job_wall_ms=int(metrics.get('job_wall_ms') or 0),
                        audio_duration_ms=int(metrics.get('audio_duration_ms') or 0),
                        realtime_x=float(metrics.get('realtime_x') or 0.0),
                        output_bytes=len(audio_bytes),
                        success=True,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    record = BenchmarkIterationRecord(iteration=round_index + 1, text=text, success=False, error_message=str(exc))
                if round_index >= run.warmup_iterations:
                    case.iterations.append(record)
                    for metric_name in ('queue_wait_ms', 'model_warm_ms', 'ttfa_ms', 'job_wall_ms', 'audio_duration_ms', 'realtime_x', 'output_bytes'):
                        value = getattr(record, metric_name)
                        if value is not None:
                            summary_values[f'{case.label}:{metric_name}'].append(float(value))
                if run.cooldown_ms:
                    await asyncio.sleep(run.cooldown_ms / 1000)
        run.summary = summary_values
        run.raw['cases'] = [case.label for case in run.cases]

    def _dataset_text(self, dataset: str | None) -> str:
        if dataset == 'de_standard_v1':
            return 'Bitte warte einen Moment, waehrend das Audio erzeugt wird.'
        return 'This is a benchmark sample.'

    def to_response(self, run: BenchmarkRunRecord) -> BenchmarkRunResponse:
        summary: dict[str, BenchmarkMetricSummary] = {}
        for key, values in run.summary.items():
            summary[key] = BenchmarkMetricSummary(
                mean=statistics.mean(values) if values else None,
                median=statistics.median(values) if values else None,
                minimum=min(values) if values else None,
                maximum=max(values) if values else None,
                stdev=statistics.pstdev(values) if len(values) > 1 else None,
            )

        cases: list[BenchmarkCaseSummary] = []
        for case in run.cases:
            case_metrics: dict[str, BenchmarkMetricSummary] = {}
            for metric_name in ('queue_wait_ms', 'model_warm_ms', 'ttfa_ms', 'job_wall_ms', 'audio_duration_ms', 'realtime_x', 'output_bytes'):
                values = run.summary.get(f'{case.label}:{metric_name}', [])
                case_metrics[metric_name] = BenchmarkMetricSummary(
                    mean=statistics.mean(values) if values else None,
                    median=statistics.median(values) if values else None,
                    minimum=min(values) if values else None,
                    maximum=max(values) if values else None,
                    stdev=statistics.pstdev(values) if len(values) > 1 else None,
                )
            cases.append(
                BenchmarkCaseSummary(
                    label=case.label,
                    request=case.request,
                    metrics=case_metrics,
                    iterations=[
                        BenchmarkIterationResult(
                            iteration=item.iteration,
                            text=item.text,
                            queue_wait_ms=item.queue_wait_ms,
                            model_warm_ms=item.model_warm_ms,
                            ttfa_ms=item.ttfa_ms,
                            job_wall_ms=item.job_wall_ms,
                            audio_duration_ms=item.audio_duration_ms,
                            realtime_x=item.realtime_x,
                            output_bytes=item.output_bytes,
                            success=item.success,
                            error_message=item.error_message,
                        )
                        for item in case.iterations
                    ],
                )
            )

        return BenchmarkRunResponse(
            run_id=run.run_id,
            name=run.name,
            dataset=run.dataset,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
            exclusive=run.exclusive,
            summary=summary,
            cases=cases,
        )
