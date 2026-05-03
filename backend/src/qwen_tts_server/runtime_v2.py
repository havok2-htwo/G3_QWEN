from __future__ import annotations

import asyncio
import gc
import hashlib
import io
import os
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .domain.models import SpeechRequest, TaskType
from .domain.state import InMemoryStore, VoiceProfileRecord


@dataclass
class BatchSynthesisItem:
    job_id: str
    sentence_index: int
    request: SpeechRequest
    text: str


@dataclass
class BatchSynthesisResult:
    job_id: str
    sentence_index: int
    sample_rate: int
    pcm: bytes
    duration_ms: int


class QwenSynthesizer:
    def __init__(self, settings: Settings, store: InMemoryStore) -> None:
        self.settings = settings
        self.store = store
        self.sample_rate = settings.sample_rate
        self._loaded_model_id: str | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._soundfile: Any | None = None
        self._numpy: Any | None = None
        self._attn_implementation = settings.attention_implementation

    def duration_ms(self, text: str) -> int:
        return max(500, 260 + len(text) * 42)

    def pcm_to_wav(self, pcm: bytes, *, sample_rate: int | None = None) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate or self.sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    async def ensure_model(self, requested_model: str | None) -> tuple[str, int]:
        target_model = requested_model or self.settings.active_model
        if not target_model:
            raise RuntimeError('No active model configured')
        if self._loaded_model_id == target_model and self._model is not None:
            return target_model, 0
        warm_ms = await asyncio.to_thread(self._load_model_sync, target_model)
        return target_model, warm_ms

    async def render_wav(self, request: SpeechRequest) -> tuple[bytes, int]:
        result = (
            await self.render_batch(
                [BatchSynthesisItem(job_id='direct', sentence_index=0, request=request, text=request.input or '')]
            )
        )[0]
        return self.pcm_to_wav(result.pcm, sample_rate=result.sample_rate), result.duration_ms

    async def render_batch(self, items: list[BatchSynthesisItem]) -> list[BatchSynthesisResult]:
        if not items:
            return []
        return await asyncio.to_thread(self._generate_batch_sync, items)

    async def stream_batch(
        self,
        items: list[BatchSynthesisItem],
        *,
        chunk_size: int = 20,
        overlap: int = 4,
    ):
        if not items:
            return

        queue: asyncio.Queue[list[BatchSynthesisResult] | BaseException | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                for results in self._generate_batch_stream_sync(items, chunk_size=chunk_size, overlap=overlap):
                    loop.call_soon_threadsafe(queue.put_nowait, results)
            except BaseException as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        thread = threading.Thread(target=worker, name='qwen-native-stream', daemon=True)
        thread.start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item

        await asyncio.to_thread(thread.join)

    def _load_model_sync(self, model_id: str) -> int:
        start = time.perf_counter()
        torch, qwen_model_cls = self._load_runtime_dependencies()
        model_source, extra_kwargs = self._resolve_model_source(model_id)
        kwargs = {
            'device_map': 'auto' if self.settings.enable_cpu_offload else self.settings.preferred_device,
            'dtype': self._torch_dtype(torch),
            'attn_implementation': self.settings.attention_implementation,
        }
        kwargs.update(extra_kwargs)

        try:
            model = qwen_model_cls.from_pretrained(model_source, **kwargs)
            self._attn_implementation = self.settings.attention_implementation
        except Exception as exc:
            if self.settings.attention_implementation != 'flash_attention_2':
                raise RuntimeError(f'Failed to load model {model_id}: {exc}') from exc
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['attn_implementation'] = 'sdpa'
            try:
                model = qwen_model_cls.from_pretrained(model_source, **fallback_kwargs)
                self._attn_implementation = 'sdpa'
            except Exception:
                raise RuntimeError(f'Failed to load model {model_id}: {exc}') from exc

        if getattr(self.settings, 'compile_model', False):
            try:
                import torch._dynamo

                torch._dynamo.config.suppress_errors = True
                if hasattr(model, 'model'):
                    model.model = self._torch.compile(model.model, mode='reduce-overhead')
                elif hasattr(model, 'llm'):
                    model.llm = self._torch.compile(model.llm, mode='reduce-overhead')
                else:
                    model = self._torch.compile(model, mode='reduce-overhead')
            except Exception:
                pass

        self._release_model()
        self._model = model
        self._loaded_model_id = model_id
        self.settings.active_model = model_id

        if getattr(self.settings, 'warmup_on_startup', True):
            self._run_warmup_inference(model_id)

        return int((time.perf_counter() - start) * 1000)

    def _run_warmup_inference(self, model_id: str) -> None:
        import logging as _logging

        _log = _logging.getLogger('qwen_tts_server.runtime')
        _log.info('warmup model_id=%s starting...', model_id)
        try:
            task_type = self._task_type_from_model(model_id)
            if task_type == TaskType.voice_design:
                self._model.generate_voice_design(
                    text=['Warmup.'],
                    language=['English'],
                    instruct=[''],
                    non_streaming_mode=False,
                )
            elif task_type == TaskType.base:
                _log.info('warmup skipped for Base model (requires prompt)')
                return
            else:
                self._model.generate_custom_voice(
                    text=['Warmup.'],
                    language=['English'],
                    speaker=[self.settings.default_voice],
                    instruct=[''],
                    non_streaming_mode=False,
                )
            _log.info('warmup model_id=%s done', model_id)
        except Exception as exc:
            _log.warning('warmup failed (non-critical): %s', exc)

    def _load_runtime_dependencies(self) -> tuple[Any, Any]:
        try:
            import numpy
            import soundfile
            import torch
            from .qwen_streaming import Qwen3TTSStreamingModel
        except Exception as exc:
            raise RuntimeError(
                'Qwen runtime dependencies are missing. Install PyTorch CUDA plus qwen-tts, numpy, and soundfile.'
            ) from exc

        if self.settings.preferred_device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('No CUDA-capable NVIDIA GPU is available for the configured runtime.')

        if torch.cuda.is_available() and self.settings.preferred_device.startswith('cuda'):
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.set_float32_matmul_precision('high')

        self._torch = torch
        self._soundfile = soundfile
        self._numpy = numpy
        return torch, Qwen3TTSStreamingModel

    def _resolve_model_source(self, model_id: str) -> tuple[str, dict[str, Any]]:
        self.settings.models_root_dir.mkdir(parents=True, exist_ok=True)
        local_dir = self.settings.models_root_dir / model_id.split('/')[-1]
        if local_dir.exists():
            return str(local_dir), {}
        if not self.settings.allow_model_downloads:
            raise RuntimeError(
                f'Model {model_id} not found under {local_dir}. Enable downloads or place the model there first.'
            )
        os.environ.setdefault('HF_HOME', str(self.settings.models_root_dir / '.hf'))
        return model_id, {'cache_dir': str(self.settings.models_root_dir)}

    def _torch_dtype(self, torch: Any) -> Any:
        mapping = {
            'float16': torch.float16,
            'bfloat16': getattr(torch, 'bfloat16', torch.float16),
            'float32': torch.float32,
        }
        return mapping.get(self.settings.torch_dtype.lower(), getattr(torch, 'bfloat16', torch.float16))

    def _release_model(self) -> None:
        if self._model is None:
            return
        self._model = None
        gc.collect()
        if self._torch is not None and self.settings.preferred_device.startswith('cuda') and self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()

    def _generate_batch_sync(self, items: list[BatchSynthesisItem]) -> list[BatchSynthesisResult]:
        if self._model is None:
            raise RuntimeError('No model loaded. Call ensure_model before generation.')
        if not items:
            return []

        texts = [item.text.strip() for item in items]
        if any(not text for text in texts):
            raise RuntimeError('Missing input text')

        task_type = items[0].request.task_type or self._task_type_from_model(self._loaded_model_id or self.settings.active_model)
        languages = [item.request.language or 'Auto' for item in items]
        generate_kwargs = self._generation_kwargs(texts)

        with self._torch.inference_mode():
            self._apply_seed(items[0].request.seed)
            if task_type == TaskType.voice_design:
                instructs = [item.request.instructions or '' for item in items]
                wavs, sample_rate = self._model.generate_voice_design(
                    text=texts,
                    language=languages,
                    instruct=instructs,
                    non_streaming_mode=False,
                    **generate_kwargs,
                )
            elif task_type == TaskType.base:
                prompts = [self._clone_prompt(item.request) for item in items]
                wavs, sample_rate = self._model.generate_voice_clone(
                    text=texts,
                    language=languages,
                    voice_clone_prompt=prompts,
                    non_streaming_mode=False,
                    **generate_kwargs,
                )
            else:
                speakers = [item.request.voice or self.settings.default_voice for item in items]
                instructs = [item.request.instructions or '' for item in items]
                wavs, sample_rate = self._model.generate_custom_voice(
                    text=texts,
                    language=languages,
                    speaker=speakers,
                    instruct=instructs,
                    non_streaming_mode=False,
                    **generate_kwargs,
                )

        results: list[BatchSynthesisResult] = []
        for item, wav in zip(items, list(wavs), strict=False):
            pcm = self._audio_array_to_pcm_bytes(wav)
            results.append(
                BatchSynthesisResult(
                    job_id=item.job_id,
                    sentence_index=item.sentence_index,
                    sample_rate=int(sample_rate),
                    pcm=pcm,
                    duration_ms=int(round(len(pcm) / 2 / max(int(sample_rate), 1) * 1000)),
                )
            )
        self.sample_rate = int(sample_rate) or self.sample_rate
        return results

    def _generate_batch_stream_sync(
        self,
        items: list[BatchSynthesisItem],
        *,
        chunk_size: int,
        overlap: int,
    ):
        if self._model is None:
            raise RuntimeError('No model loaded. Call ensure_model before generation.')
        if not items:
            return

        texts = [item.text.strip() for item in items]
        if any(not text for text in texts):
            raise RuntimeError('Missing input text')

        task_type = items[0].request.task_type or self._task_type_from_model(self._loaded_model_id or self.settings.active_model)
        if task_type != TaskType.base:
            raise RuntimeError('Native Qwen streaming is currently available for Base voice cloning only.')
        if not hasattr(self._model, 'generate_batch_voice_clone_stream'):
            raise RuntimeError('Loaded Qwen runtime does not expose native streaming.')

        prompts = [self._clone_prompt(item.request) for item in items]
        prompt_dict = self._model._prompt_items_to_voice_clone_prompt(prompts)
        language = items[0].request.language or 'Auto'
        sample_rate = int(self._model.model.speech_tokenizer.get_output_sample_rate())
        generate_kwargs = self._generation_kwargs(texts)

        with self._torch.inference_mode():
            self._apply_seed(items[0].request.seed)
            stream = self._model.generate_batch_voice_clone_stream(
                texts=texts,
                language=language,
                voice_clone_prompt=prompt_dict,
                chunk_size=chunk_size,
                overlap=overlap,
                max_new_tokens=generate_kwargs['max_new_tokens'],
            )

            for audio_chunks, _finished in stream:
                results: list[BatchSynthesisResult] = []
                for item, audio in zip(items, audio_chunks, strict=False):
                    if audio is None:
                        continue
                    pcm = self._audio_array_to_pcm_bytes(audio)
                    if not pcm:
                        continue
                    results.append(
                        BatchSynthesisResult(
                            job_id=item.job_id,
                            sentence_index=item.sentence_index,
                            sample_rate=sample_rate,
                            pcm=pcm,
                            duration_ms=int(round(len(pcm) / 2 / max(sample_rate, 1) * 1000)),
                        )
                    )
                if results:
                    self.sample_rate = sample_rate or self.sample_rate
                    yield results

    def _generation_kwargs(self, texts: list[str]) -> dict[str, int]:
        longest_text = max((len(text) for text in texts), default=0)
        max_new_tokens = min(4096, max(384, longest_text * 6 + 192))
        return {'max_new_tokens': max_new_tokens}

    def _apply_seed(self, seed: int | None) -> None:
        if seed is None or self._torch is None:
            return
        normalized_seed = int(seed) % (2**31)
        self._torch.manual_seed(normalized_seed)
        if self._torch.cuda.is_available():
            self._torch.cuda.manual_seed_all(normalized_seed)

    def _task_type_from_model(self, model_id: str | None) -> TaskType:
        if not model_id:
            return TaskType.custom_voice
        if model_id.endswith('VoiceDesign'):
            return TaskType.voice_design
        if model_id.endswith('Base'):
            return TaskType.base
        return TaskType.custom_voice

    def _clone_prompt(self, request: SpeechRequest) -> Any:
        if self._model is None:
            raise RuntimeError('No model loaded. Call ensure_model before generation.')
        voice_profile = self._resolve_voice_profile(request.voice)
        if voice_profile and voice_profile.audio_bytes:
            ref_text = request.ref_text or voice_profile.ref_text
            if not request.x_vector_only_mode and not (ref_text or '').strip():
                raise RuntimeError('Base voice cloning requires ref_text for the saved voice profile, or x_vector_only_mode=true.')
            prompt_key = self._voice_prompt_cache_key(
                profile=voice_profile,
                model_id=self._loaded_model_id or self.settings.active_model or '',
                ref_text=ref_text,
                x_vector_only_mode=request.x_vector_only_mode,
            )
            cached = self.store.prompt_cache.get(prompt_key)
            if cached is not None:
                return cached
            prompt_items = self._model.create_voice_clone_prompt(
                ref_audio=self._audio_bytes_to_prompt_audio(voice_profile),
                ref_text=ref_text,
                x_vector_only_mode=request.x_vector_only_mode,
            )
            prompt = prompt_items[0]
            self.store.prompt_cache[prompt_key] = prompt
            return prompt

        if request.ref_audio:
            if not request.x_vector_only_mode and not (request.ref_text or '').strip():
                raise RuntimeError('Base voice cloning requires ref_text with ref_audio, or x_vector_only_mode=true.')
            prompt_key = hashlib.sha1(
                '|'.join(
                    [
                        self._loaded_model_id or self.settings.active_model or '',
                        request.ref_audio[:256],
                        request.ref_text or '',
                        str(request.x_vector_only_mode),
                    ]
                ).encode('utf-8')
            ).hexdigest()
            cached = self.store.prompt_cache.get(prompt_key)
            if cached is not None:
                return cached
            prompt_items = self._model.create_voice_clone_prompt(
                ref_audio=request.ref_audio,
                ref_text=request.ref_text,
                x_vector_only_mode=request.x_vector_only_mode,
            )
            prompt = prompt_items[0]
            self.store.prompt_cache[prompt_key] = prompt
            return prompt

        raise RuntimeError('Base voice cloning requires a saved voice profile or ref_audio + ref_text.')

    def _voice_prompt_cache_key(
        self,
        *,
        profile: VoiceProfileRecord,
        model_id: str,
        ref_text: str | None,
        x_vector_only_mode: bool,
    ) -> str:
        fingerprint = hashlib.sha1(profile.audio_bytes or b'').hexdigest()
        return '|'.join(
            [
                model_id,
                profile.voice_id,
                profile.name,
                fingerprint,
                ref_text or '',
                str(x_vector_only_mode),
            ]
        )

    def _resolve_voice_profile(self, voice_name: str | None) -> VoiceProfileRecord | None:
        if not voice_name:
            return None
        for profile in self.store.voice_profiles.values():
            if profile.voice_id == voice_name or profile.name == voice_name:
                return profile
        return None

    def _audio_bytes_to_prompt_audio(self, profile: VoiceProfileRecord) -> tuple[Any, int]:
        if self._soundfile is None:
            self._load_runtime_dependencies()
        buffer = io.BytesIO(profile.audio_bytes or b'')
        audio, sample_rate = self._soundfile.read(buffer, dtype='float32', always_2d=False)
        return audio, int(sample_rate)

    def _audio_array_to_pcm_bytes(self, audio: Any) -> bytes:
        if self._numpy is None:
            self._load_runtime_dependencies()
        array = audio
        if hasattr(array, 'detach'):
            array = array.detach().float().cpu().numpy()
        else:
            array = self._numpy.asarray(array)
        if array.ndim > 1:
            array = array.reshape(-1)
        array = self._numpy.clip(array.astype('float32'), -1.0, 1.0)
        return (array * 32767.0).astype(self._numpy.int16).tobytes()


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

    def pcm_to_wav(self, pcm: bytes, *, sample_rate: int | None = None) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate or self.sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    async def render_batch(self, items: list[BatchSynthesisItem]) -> list[BatchSynthesisResult]:
        results: list[BatchSynthesisResult] = []
        for item in items:
            text = item.text or ''
            duration_ms = self.duration_ms(text)
            total_samples = int(self.sample_rate * duration_ms / 1000)
            freq = self.frequency_for(text)
            amplitude = 10_000
            frames = bytearray()
            for sample_index in range(total_samples):
                value = int(amplitude * __import__('math').sin(2 * __import__('math').pi * freq * sample_index / self.sample_rate))
                frames.extend(int(value).to_bytes(2, byteorder='little', signed=True))
            results.append(
                BatchSynthesisResult(
                    job_id=item.job_id,
                    sentence_index=item.sentence_index,
                    sample_rate=self.sample_rate,
                    pcm=bytes(frames),
                    duration_ms=duration_ms,
                )
            )
        return results


def build_synthesizer(settings: Settings, store: InMemoryStore) -> Any:
    if settings.runtime_backend.lower() == 'mock':
        return MockSynthesizer(sample_rate=settings.sample_rate)
    if settings.runtime_backend.lower() != 'qwen':
        raise RuntimeError(f'Unsupported runtime backend: {settings.runtime_backend}')
    return QwenSynthesizer(settings=settings, store=store)


def query_nvidia_smi() -> dict[str, int | str | None]:
    try:
        result = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return {
            'name': 'Unavailable',
            'memory_used_mb': 0,
            'memory_total_mb': 0,
            'utilization_percent': 0,
            'temperature_c': None,
        }

    line = next((entry.strip() for entry in result.stdout.splitlines() if entry.strip()), '')
    if not line:
        return {
            'name': 'Unavailable',
            'memory_used_mb': 0,
            'memory_total_mb': 0,
            'utilization_percent': 0,
            'temperature_c': None,
        }

    name, memory_used, memory_total, utilization, temperature = [part.strip() for part in line.split(',', maxsplit=4)]
    return {
        'name': name,
        'memory_used_mb': int(memory_used),
        'memory_total_mb': int(memory_total),
        'utilization_percent': int(utilization),
        'temperature_c': int(temperature) if temperature else None,
    }
