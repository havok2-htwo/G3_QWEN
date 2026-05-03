from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import statistics
import uuid
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
    TaskType,
    TranscriptionResponse,
)
from .domain.state import InMemoryStore, JobRecord, RequestState, utcnow
from .prompt_batch import chunk_pcm16le, split_sentences
from .runtime_v2 import BatchSynthesisItem, query_nvidia_smi


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


class QueueService:
    def __init__(self, store: InMemoryStore, synthesizer: Any, events: EventHub, settings: Settings) -> None:
        self.store = store
        self.synthesizer = synthesizer
        self.events = events
        self.settings = settings

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

    async def submit(self, request: SpeechRequest, *, owner_scope: str = 'public') -> JobRecord:
        text = (request.input or '').strip()
        if not text:
            raise RuntimeError('Missing input text')
        self._validate_request_voice_model(request)

        sentences = split_sentences(
            text,
            enabled=self.settings.sentence_chunking,
            short_sentence_merge_max_chars=self.settings.short_sentence_merge_max_chars,
            following_sentence_merge_min_chars=self.settings.following_sentence_merge_min_chars,
        )
        if not sentences:
            raise RuntimeError('Missing input text')

        now = utcnow()
        job = JobRecord(
            job_id=f'job_{now.timestamp():.0f}_{len(self.store.jobs):04d}',
            request=request,
            created_at=now,
            updated_at=now,
            owner_scope=owner_scope,
            sentences_total=len(sentences),
        )
        job.metrics['sentences_total'] = len(sentences)
        state = RequestState(job_id=job.job_id, group_key=self._group_key_for_request(request), sentences=sentences)

        async with self.store.job_condition:
            total_outstanding = len(self.store.waiting_requests) + len(self.store.active_request_ids)
            if total_outstanding >= self.store.max_queue_size:
                raise RuntimeError('Queue saturated')
            self.store.jobs[job.job_id] = job
            self.store.request_states[job.job_id] = state
            self.store.waiting_requests.append(job.job_id)
            self._recompute_positions_locked()
            job.stream_events.put_nowait(
                {
                    'type': 'start',
                    'job_id': job.job_id,
                    'sentence_count': len(sentences),
                    'queue_position': job.queue_position,
                }
            )
            self.store.job_condition.notify_all()

        await self._publish_state()
        return job

    async def cancel(self, job_id: str) -> JobRecord:
        async with self.store.job_condition:
            job = self.store.jobs[job_id]
            if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
                raise RuntimeError('Job already finished')
            if job.status == JobStatus.cancelling:
                return job

            job.cancel_requested = True
            job.updated_at = utcnow()

            if job_id in self.store.waiting_requests:
                try:
                    self.store.waiting_requests.remove(job_id)
                except ValueError:
                    pass
                self._mark_cancelled_locked(job, 'Cancelled before execution.')
                self._recompute_positions_locked()
                self.store.job_condition.notify_all()
            else:
                job.status = JobStatus.cancelling
                job.error_message = 'Cancellation requested.'
                self.store.job_condition.notify_all()

        await self._publish_state()
        return job

    async def delete(self, job_id: str) -> None:
        async with self.store.job_condition:
            job = self.store.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
                job.cancel_requested = True
                job.status = JobStatus.cancelling
                job.error_message = 'Cancellation requested.'
            else:
                self.store.jobs.pop(job_id, None)
                self.store.request_states.pop(job_id, None)
                if job_id in self.store.waiting_requests:
                    self.store.waiting_requests.remove(job_id)
                if job_id in self.store.active_request_ids:
                    self.store.active_request_ids.remove(job_id)
                self._recompute_positions_locked()
            self.store.job_condition.notify_all()

        await self._publish_state()

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
            'active_requests': self.store.active_requests(),
            'active_model': self.store.active_model,
            'worker_state': self.store.worker_state,
        }

    async def _worker_loop(self) -> None:
        self.store.worker_state = 'idle'
        await self._publish_state()

        while not self.store.worker_stop.is_set():
            batch_items: list[BatchSynthesisItem] = []
            current_batch: dict[str, Any] | None = None
            involved_job_ids: list[str] = []
            anchor_request: SpeechRequest | None = None

            async with self.store.job_condition:
                await self.store.job_condition.wait_for(
                    lambda: self.store.worker_stop.is_set()
                    or bool(self.store.waiting_requests)
                    or any(
                        self.store.request_states.get(job_id) and self.store.request_states[job_id].has_pending_sentences()
                        for job_id in self.store.active_request_ids
                    )
                )
                if self.store.worker_stop.is_set():
                    break

                self._promote_waiting_locked()
                if self._should_batch_wait_locked():
                    try:
                        await asyncio.wait_for(
                            self.store.job_condition.wait(),
                            timeout=self.settings.batch_wait_ms / 1000.0,
                        )
                    except asyncio.TimeoutError:
                        pass
                    if self.store.worker_stop.is_set():
                        break
                    self._promote_waiting_locked()

                batch_plan = self._build_batch_plan_locked()
                if not batch_plan:
                    continue

                batch_items, current_batch, involved_job_ids = self._reserve_batch_locked(batch_plan)
                if not batch_items or current_batch is None:
                    continue

                anchor_request = batch_items[0].request
                self.store.current_batch = current_batch
                self.store.worker_state = 'warming'
                for job_id in involved_job_ids:
                    job = self.store.jobs[job_id]
                    if job.started_at is None:
                        job.started_at = utcnow()
                        job.metrics['queue_wait_ms'] = int((job.started_at - job.created_at).total_seconds() * 1000)
                    if job.status not in {JobStatus.cancelling, JobStatus.cancelled}:
                        target_model = job.request.model or self.store.active_model
                        job.status = JobStatus.warming if target_model and target_model != self.store.active_model else JobStatus.running
                    job.updated_at = utcnow()

            await self._publish_state()

            try:
                model_used, warm_ms = await self.synthesizer.ensure_model(anchor_request.model if anchor_request else None)
                self.store.worker_state = 'running'
                if self._can_use_native_streaming(batch_items):
                    await self._process_native_streaming_batch(
                        batch_items=batch_items,
                        involved_job_ids=involved_job_ids,
                        current_batch=current_batch,
                        model_used=model_used,
                        warm_ms=warm_ms,
                    )
                    await self._publish_state()
                    continue
                results = await self.synthesizer.render_batch(batch_items)
            except Exception as exc:
                async with self.store.job_condition:
                    self.store.current_batch = None
                    self.store.worker_state = 'idle'
                    for job_id in involved_job_ids:
                        job = self.store.jobs.get(job_id)
                        if job is None:
                            continue
                        if job.cancel_requested:
                            self._mark_cancelled_locked(job, 'Cancelled during synthesis.')
                        else:
                            self._fail_job_locked(job, str(exc))
                    self._recompute_positions_locked()
                    self.store.job_condition.notify_all()
                await self._publish_state()
                continue

            async with self.store.job_condition:
                self.store.current_batch = None
                self.store.worker_state = 'idle'
                self.store.active_model = model_used
                self.store.models_loaded.add(model_used)
                self.store.recent_batches.append(current_batch)
                results_by_key = {(item.job_id, item.sentence_index): item for item in results}

                for job_id in involved_job_ids:
                    job = self.store.jobs.get(job_id)
                    state = self.store.request_states.get(job_id)
                    if job is None or state is None:
                        continue

                    job.model_used = model_used
                    if job.metrics.get('model_warm_ms') is None:
                        job.metrics['model_warm_ms'] = warm_ms
                    job.metrics['batch_count'] = state.batch_count

                    if job.cancel_requested:
                        self._mark_cancelled_locked(job, 'Cancelled during synthesis.')
                        continue

                    for sentence_index in list(state.inflight_sentence_indices):
                        result = results_by_key.get((job_id, sentence_index))
                        if result is None:
                            self._fail_job_locked(job, 'Batch result was incomplete.')
                            break
                        state.ready_sentence_pcm[sentence_index] = result.pcm
                        state.sentence_duration_ms[sentence_index] = result.duration_ms
                        state.sample_rate = result.sample_rate
                        state.inflight_sentence_indices.discard(sentence_index)

                    if job.status == JobStatus.failed:
                        continue

                    self._flush_ready_sentences_locked(job, state)
                    if state.is_complete():
                        self._complete_job_locked(job, state)

                self._recompute_positions_locked()
                self.store.job_condition.notify_all()

            await self._publish_state()

        self.store.worker_state = 'stopped'
        await self._publish_state()

    def _can_use_native_streaming(self, batch_items: list[BatchSynthesisItem]) -> bool:
        if not batch_items or not hasattr(self.synthesizer, 'stream_batch'):
            return False
        return all(item.request.stream and self._task_type_for_request(item.request) == TaskType.base for item in batch_items)

    async def _process_native_streaming_batch(
        self,
        *,
        batch_items: list[BatchSynthesisItem],
        involved_job_ids: list[str],
        current_batch: dict[str, Any],
        model_used: str,
        warm_ms: int,
    ) -> None:
        durations_by_key: dict[tuple[str, int], int] = {}
        batch_id = str(current_batch.get('batch_id') or '')

        async for results in self.synthesizer.stream_batch(
            batch_items,
            chunk_size=max(2, int(self.settings.stream_chunk_ms / 10)),
            overlap=4,
        ):
            async with self.store.job_condition:
                self.store.active_model = model_used
                self.store.models_loaded.add(model_used)
                for result in results:
                    job = self.store.jobs.get(result.job_id)
                    state = self.store.request_states.get(result.job_id)
                    if job is None or state is None:
                        continue
                    if job.cancel_requested:
                        self._mark_cancelled_locked(job, 'Cancelled during native streaming.')
                        continue

                    key = (result.job_id, result.sentence_index)
                    durations_by_key[key] = durations_by_key.get(key, 0) + result.duration_ms

                    job.model_used = model_used
                    job.sample_rate = result.sample_rate
                    state.sample_rate = result.sample_rate
                    job.status = JobStatus.streaming
                    job.updated_at = utcnow()
                    if job.metrics.get('model_warm_ms') is None:
                        job.metrics['model_warm_ms'] = warm_ms
                    job.metrics['batch_count'] = state.batch_count

                    if result.sentence_index == state.next_emit_sentence_index:
                        prebuffer_ms = max(0, int(getattr(self.settings, 'stream_prebuffer_ms', 0)))
                        already_started = state.chunk_index_by_sentence.get(result.sentence_index, 0) > 0
                        if prebuffer_ms > 0 and not already_started:
                            state.pending_preview_pcm.setdefault(result.sentence_index, []).append(result.pcm)
                            state.pending_preview_duration_ms.setdefault(result.sentence_index, []).append(result.duration_ms)
                            buffered_ms = sum(state.pending_preview_duration_ms.get(result.sentence_index, []))
                            if buffered_ms >= prebuffer_ms:
                                pending_chunks = state.pending_preview_pcm.pop(result.sentence_index, [])
                                state.pending_preview_duration_ms.pop(result.sentence_index, None)
                                for chunk in pending_chunks:
                                    self._emit_native_chunk_locked(
                                        job,
                                        state,
                                        sentence_index=result.sentence_index,
                                        pcm=chunk,
                                        sample_rate=result.sample_rate,
                                        batch_id=batch_id,
                                    )
                        else:
                            self._emit_native_chunk_locked(
                                job,
                                state,
                                sentence_index=result.sentence_index,
                                pcm=result.pcm,
                                sample_rate=result.sample_rate,
                                batch_id=batch_id,
                            )
                    else:
                        state.pending_preview_pcm.setdefault(result.sentence_index, []).append(result.pcm)
                        state.pending_preview_duration_ms.setdefault(result.sentence_index, []).append(result.duration_ms)
                self.store.job_condition.notify_all()

        async with self.store.job_condition:
            self.store.current_batch = None
            self.store.worker_state = 'idle'
            self.store.active_model = model_used
            self.store.models_loaded.add(model_used)
            self.store.recent_batches.append(current_batch)

            for item in batch_items:
                job = self.store.jobs.get(item.job_id)
                state = self.store.request_states.get(item.job_id)
                if job is None or state is None:
                    continue
                if job.cancel_requested:
                    self._mark_cancelled_locked(job, 'Cancelled during native streaming.')
                    continue

                key = (item.job_id, item.sentence_index)
                duration_ms = durations_by_key.get(key, 0)
                state.sentence_duration_ms.pop(item.sentence_index, None)
                state.ready_sentence_pcm.pop(item.sentence_index, None)
                state.inflight_sentence_indices.discard(item.sentence_index)
                if duration_ms <= 0:
                    self._fail_job_locked(job, 'Native stream produced no audio for a sentence.')
                    continue
                state.completed_streaming_sentence_indices.add(item.sentence_index)
                self._flush_native_streaming_sentences_locked(job, state, batch_id=batch_id)

                job.updated_at = utcnow()
                job.metrics['batch_count'] = state.batch_count
                if state.is_complete():
                    self._complete_job_locked(job, state)
                elif job.status not in {JobStatus.failed, JobStatus.cancelled, JobStatus.cancelling}:
                    job.status = JobStatus.running

            self._recompute_positions_locked()
            self.store.job_condition.notify_all()

    def _should_batch_wait_locked(self) -> bool:
        if self.settings.batch_wait_ms <= 0:
            return False
        if len(self.store.active_request_ids) >= self.settings.max_parallel_requests and not self.store.waiting_requests:
            return False
        has_new_request = False
        for job_id in self.store.active_request_ids:
            state = self.store.request_states.get(job_id)
            job = self.store.jobs.get(job_id)
            if state is not None and job is not None and not job.cancel_requested and state.batch_count == 0:
                has_new_request = True
                break
        if not has_new_request and not self.store.waiting_requests:
            return False
        return 0 < len(self._build_batch_plan_locked()) < self.settings.max_batch_size

    def _emit_native_chunk_locked(
        self,
        job: JobRecord,
        state: RequestState,
        *,
        sentence_index: int,
        pcm: bytes,
        sample_rate: int,
        batch_id: str,
        final_chunk_of_sentence: bool = False,
    ) -> None:
        if not pcm:
            return
        job.sample_rate = sample_rate
        state.sample_rate = sample_rate
        job.status = JobStatus.streaming
        job.updated_at = utcnow()
        if job.first_audio_at is None:
            job.first_audio_at = utcnow()
            if job.started_at:
                job.metrics['ttfa_ms'] = int((job.first_audio_at - job.started_at).total_seconds() * 1000)

        job.pcm_parts.append(pcm)
        chunk_index = state.chunk_index_by_sentence.get(sentence_index, 0)
        state.chunk_index_by_sentence[sentence_index] = chunk_index + 1
        emitted_samples = state.emitted_samples_by_sentence.get(sentence_index, 0) + len(pcm) // 2
        state.emitted_samples_by_sentence[sentence_index] = emitted_samples
        state.emitted_audio_ms += int(len(pcm) / 2 / max(sample_rate, 1) * 1000)
        progress_step = min(sentence_index + 1, len(state.sentences))
        job.stream_chunks.put_nowait(pcm)
        job.stream_events.put_nowait(
            {
                'type': 'chunk',
                'job_id': job.job_id,
                'sentence_index': sentence_index,
                'chunk_index': chunk_index,
                'sample_rate': sample_rate,
                'pcm16_b64': base64.b64encode(pcm).decode('ascii'),
                'emitted_audio_ms': state.emitted_audio_ms,
                'preview': True,
                'final_chunk_of_sentence': final_chunk_of_sentence,
                'progress_step': progress_step,
                'native_stream': True,
                'batch_id': batch_id,
            }
        )

    def _flush_native_streaming_sentences_locked(self, job: JobRecord, state: RequestState, *, batch_id: str) -> None:
        while state.next_emit_sentence_index in state.completed_streaming_sentence_indices:
            sentence_index = state.next_emit_sentence_index
            pending_chunks = state.pending_preview_pcm.pop(sentence_index, [])
            state.pending_preview_duration_ms.pop(sentence_index, None)
            for chunk_index, chunk in enumerate(pending_chunks):
                self._emit_native_chunk_locked(
                    job,
                    state,
                    sentence_index=sentence_index,
                    pcm=chunk,
                    sample_rate=state.sample_rate,
                    batch_id=batch_id,
                    final_chunk_of_sentence=chunk_index == len(pending_chunks) - 1,
                )
            state.completed_streaming_sentence_indices.discard(sentence_index)
            state.next_emit_sentence_index += 1
            job.metrics['sentences_rendered'] = state.next_emit_sentence_index

    def _promote_waiting_locked(self) -> None:
        while self.store.waiting_requests and len(self.store.active_request_ids) < self.settings.max_parallel_requests:
            job_id = self.store.waiting_requests.popleft()
            if job_id not in self.store.jobs or job_id not in self.store.request_states:
                continue
            job = self.store.jobs[job_id]
            if job.cancel_requested:
                self._mark_cancelled_locked(job, 'Cancelled before execution.')
                continue
            self.store.active_request_ids.append(job_id)
            job.queue_position = 0
            job.eta_ms = 0

    def _build_batch_plan_locked(self) -> list[tuple[str, int]]:
        anchor_job_id = next(
            (
                job_id
                for job_id in self.store.active_request_ids
                if job_id in self.store.request_states
                and self.store.request_states[job_id].has_pending_sentences()
                and not self.store.jobs[job_id].cancel_requested
            ),
            None,
        )
        if anchor_job_id is None:
            return []

        anchor_group = self.store.request_states[anchor_job_id].group_key
        eligible = [
            job_id
            for job_id in self.store.active_request_ids
            if job_id in self.store.request_states
            and self.store.request_states[job_id].group_key == anchor_group
            and self.store.request_states[job_id].has_pending_sentences()
            and not self.store.jobs[job_id].cancel_requested
        ]
        if not eligible:
            return []

        plan: list[tuple[str, int]] = []
        per_request_offsets = {job_id: 0 for job_id in eligible}
        made_progress = True
        while len(plan) < self.settings.max_batch_size and made_progress:
            made_progress = False
            for job_id in eligible:
                state = self.store.request_states[job_id]
                offset = per_request_offsets[job_id]
                if offset >= len(state.pending_sentence_indices):
                    continue
                plan.append((job_id, state.pending_sentence_indices[offset]))
                per_request_offsets[job_id] = offset + 1
                made_progress = True
                if len(plan) >= self.settings.max_batch_size:
                    break
        return plan

    def _reserve_batch_locked(
        self,
        batch_plan: list[tuple[str, int]],
    ) -> tuple[list[BatchSynthesisItem], dict[str, Any] | None, list[str]]:
        if not batch_plan:
            return [], None, []

        batch_id = uuid.uuid4().hex[:8]
        items: list[BatchSynthesisItem] = []
        involved_job_ids: list[str] = []
        sentence_indices: list[int] = []
        request_ids: list[str] = []
        task_type = None
        voice = None
        language = None
        model_id = None

        incremented_batch_count: set[str] = set()
        for job_id, expected_sentence_index in batch_plan:
            state = self.store.request_states[job_id]
            actual = state.pending_sentence_indices.popleft()
            if actual != expected_sentence_index:
                raise RuntimeError(
                    f'Sentence queue order drifted for request {job_id}: expected {expected_sentence_index}, got {actual}.'
                )
            state.inflight_sentence_indices.add(actual)
            if job_id not in incremented_batch_count:
                state.batch_count += 1
                incremented_batch_count.add(job_id)
            job = self.store.jobs[job_id]
            items.append(
                BatchSynthesisItem(
                    job_id=job_id,
                    sentence_index=actual,
                    request=job.request.model_copy(update={'input': state.sentences[actual]}),
                    text=state.sentences[actual],
                )
            )
            if job_id not in involved_job_ids:
                involved_job_ids.append(job_id)
            request_ids.append(job_id)
            sentence_indices.append(actual)
            model_id = job.request.model or self.store.active_model or self.settings.active_model
            task_type = job.request.task_type or self._task_type_for_request(job.request)
            voice = job.request.voice
            language = job.request.language or 'Auto'
            job.stream_events.put_nowait(
                {
                    'type': 'batch',
                    'job_id': job_id,
                    'batch_id': batch_id,
                    'sentence_index': actual,
                    'batch_size': len(batch_plan),
                }
            )

        for job_id in involved_job_ids:
            if job_id in self.store.active_request_ids:
                self.store.active_request_ids.remove(job_id)
                self.store.active_request_ids.append(job_id)

        current_batch = {
            'batch_id': batch_id,
            'model_id': model_id or '',
            'task_type': task_type.value if isinstance(task_type, TaskType) else str(task_type),
            'voice': voice,
            'language': language,
            'size': len(items),
            'started_at': utcnow(),
            'request_ids': request_ids,
            'sentence_indices': sentence_indices,
        }
        return items, current_batch, involved_job_ids

    def _flush_ready_sentences_locked(self, job: JobRecord, state: RequestState) -> None:
        while state.next_emit_sentence_index in state.ready_sentence_pcm:
            sentence_index = state.next_emit_sentence_index
            pcm = state.ready_sentence_pcm.pop(sentence_index)
            duration_ms = state.sentence_duration_ms.pop(sentence_index, 0)
            job.sample_rate = state.sample_rate
            job.pcm_parts.append(pcm)
            previous_audio_ms = state.emitted_audio_ms
            state.next_emit_sentence_index += 1
            state.emitted_audio_ms += duration_ms
            job.updated_at = utcnow()
            job.metrics['sentences_rendered'] = state.next_emit_sentence_index

            if job.first_audio_at is None:
                job.first_audio_at = utcnow()
                if job.started_at:
                    job.metrics['ttfa_ms'] = int((job.first_audio_at - job.started_at).total_seconds() * 1000)

            if job.request.stream:
                job.status = JobStatus.streaming
                emitted_samples = 0
                for chunk_index, chunk in enumerate(
                    chunks := list(chunk_pcm16le(pcm, sample_rate=job.sample_rate, chunk_ms=self.settings.stream_chunk_ms))
                ):
                    emitted_samples += len(chunk) // 2
                    job.stream_chunks.put_nowait(chunk)
                    job.stream_events.put_nowait(
                        {
                            'type': 'chunk',
                            'job_id': job.job_id,
                            'sentence_index': sentence_index,
                            'chunk_index': chunk_index,
                            'sample_rate': job.sample_rate,
                            'pcm16_b64': base64.b64encode(chunk).decode('ascii'),
                            'emitted_audio_ms': previous_audio_ms + int(emitted_samples / max(job.sample_rate, 1) * 1000),
                            'preview': False,
                            'final_chunk_of_sentence': chunk_index == len(chunks) - 1,
                            'progress_step': sentence_index + 1,
                            'native_stream': False,
                        }
                    )
            else:
                job.status = JobStatus.running

    def _complete_job_locked(self, job: JobRecord, state: RequestState) -> None:
        combined_pcm = b''.join(job.pcm_parts)
        job.final_audio = self.synthesizer.pcm_to_wav(combined_pcm, sample_rate=job.sample_rate)
        job.content_type = 'audio/wav'
        job.completed_at = utcnow()
        job.updated_at = job.completed_at
        job.status = JobStatus.completed
        job.error_message = None
        job.metrics['audio_duration_ms'] = state.emitted_audio_ms
        if job.started_at:
            job.metrics['job_wall_ms'] = int((job.completed_at - job.started_at).total_seconds() * 1000)
        job.metrics['output_bytes'] = len(job.final_audio or b'')
        job.metrics['batch_count'] = state.batch_count
        wall_ms = max(int(job.metrics.get('job_wall_ms') or 1), 1)
        duration_ms = int(job.metrics.get('audio_duration_ms') or 0)
        job.metrics['realtime_x'] = round(duration_ms / wall_ms, 3) if duration_ms else 0.0

        self.store.total_jobs_completed += 1
        self.store.total_audio_seconds += duration_ms / 1000.0
        self.store.completed_job_metrics.append(job.metrics.copy())

        self.store.request_states.pop(job.job_id, None)
        if job.job_id in self.store.active_request_ids:
            self.store.active_request_ids.remove(job.job_id)

        if job.request.stream:
            job.stream_events.put_nowait(
                {
                    'type': 'done',
                    'result': {
                        'job_id': job.job_id,
                        'status': job.status.value,
                        'sample_rate': job.sample_rate,
                        'metrics': job.metrics,
                    },
                }
            )
            job.stream_events.put_nowait(None)
            job.stream_chunks.put_nowait(None)

    def _fail_job_locked(self, job: JobRecord, message: str) -> None:
        now = utcnow()
        job.status = JobStatus.failed
        job.error_message = message
        job.completed_at = now
        job.updated_at = now
        job.final_audio = None
        job.content_type = None
        self.store.request_states.pop(job.job_id, None)
        if job.job_id in self.store.active_request_ids:
            self.store.active_request_ids.remove(job.job_id)
        if job.job_id in self.store.waiting_requests:
            self.store.waiting_requests.remove(job.job_id)
        if job.request.stream:
            job.stream_events.put_nowait({'type': 'error', 'message': message})
            job.stream_events.put_nowait(None)
            job.stream_chunks.put_nowait(None)

    def _mark_cancelled_locked(self, job: JobRecord, message: str) -> None:
        now = utcnow()
        job.cancel_requested = True
        job.status = JobStatus.cancelled
        job.completed_at = now
        job.updated_at = now
        job.queue_position = 0
        job.eta_ms = 0
        job.error_message = message
        self.store.request_states.pop(job.job_id, None)
        if job.job_id in self.store.active_request_ids:
            self.store.active_request_ids.remove(job.job_id)
        if job.job_id in self.store.waiting_requests:
            self.store.waiting_requests.remove(job.job_id)
        if job.request.stream:
            job.stream_events.put_nowait({'type': 'error', 'message': message})
            job.stream_events.put_nowait(None)
            job.stream_chunks.put_nowait(None)

    def _recompute_positions_locked(self) -> None:
        for index, job_id in enumerate(self.store.waiting_requests, start=1):
            job = self.store.jobs[job_id]
            job.queue_position = index
            job.eta_ms = self.store.estimate_eta_ms(index, len(job.request.input or ''))
            job.updated_at = utcnow()

    def _task_type_for_request(self, request: SpeechRequest) -> TaskType:
        if request.task_type is not None:
            return request.task_type
        model_id = request.model or self.store.active_model or self.settings.active_model
        if model_id.endswith('VoiceDesign'):
            return TaskType.voice_design
        if model_id.endswith('Base'):
            return TaskType.base
        return TaskType.custom_voice

    def _voice_profile_for_request(self, request: SpeechRequest):
        voice = request.voice
        if not voice:
            return None
        for profile in self.store.voice_profiles.values():
            if profile.voice_id == voice or profile.name == voice:
                return profile
        return None

    def _validate_request_voice_model(self, request: SpeechRequest) -> None:
        task_type = self._task_type_for_request(request)
        voice_profile = self._voice_profile_for_request(request)
        if task_type == TaskType.custom_voice and voice_profile is not None:
            raise RuntimeError(
                f'Voice profile "{voice_profile.name}" requires a Base model. '
                'Select Qwen3-TTS-12Hz-1.7B-Base or Qwen3-TTS-12Hz-0.6B-Base.'
            )
        if task_type == TaskType.base and voice_profile is None and not request.ref_audio:
            raise RuntimeError('Base voice cloning requires a saved custom voice profile or ref_audio + ref_text.')

    def _group_key_for_request(self, request: SpeechRequest) -> str:
        task_type = self._task_type_for_request(request)
        model_id = request.model or self.store.active_model or self.settings.active_model
        language = (request.language or 'Auto').strip()
        instructions_hash = hashlib.sha1((request.instructions or '').encode('utf-8')).hexdigest()[:12]
        seed_key = str(request.seed) if request.seed is not None else 'random'
        if task_type == TaskType.base:
            voice_key = request.voice or hashlib.sha1(
                f"{request.ref_audio or ''}|{request.ref_text or ''}|{request.x_vector_only_mode}".encode('utf-8')
            ).hexdigest()[:12]
        elif task_type == TaskType.voice_design:
            voice_key = instructions_hash
        else:
            voice_key = request.voice or self.settings.default_voice
        return '|'.join([model_id, task_type.value, language, voice_key, instructions_hash, seed_key])

    async def _publish_state(self) -> None:
        await self.events.publish('dashboard.snapshot', self.queue_snapshot())


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
            transcription=f'Mock transcription for {filename or "audio"} ({len(data)} bytes).',
            voice_vector=[0.1, -0.2, 0.3],
        )
