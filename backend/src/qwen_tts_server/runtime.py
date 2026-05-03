from __future__ import annotations

import asyncio
import gc
import io
import os
import re
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？…])\s+|\n+")

def _split_sentences(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    if not parts:
        return [cleaned]

    merged_parts = []
    index = 0
    while index < len(parts):
        current = parts[index]
        if (
            len(current) <= 30
            and current.endswith(("!", "?", "！", "？"))
            and index + 1 < len(parts)
            and len(parts[index + 1]) >= 20
        ):
            merged_parts.append(f"{current} {parts[index + 1]}")
            index += 2
            continue
        merged_parts.append(current)
        index += 1
    return merged_parts

from .config import Settings
from .domain.models import SpeechRequest, TaskType
from .domain.state import InMemoryStore, VoiceProfileRecord


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

    def pcm_to_wav(self, pcm: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    def wav_to_pcm(self, wav_bytes: bytes) -> bytes:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
            return wav_file.readframes(wav_file.getnframes())

    async def ensure_model(self, requested_model: str | None) -> tuple[str, int]:
        target_model = requested_model or self.settings.active_model
        if not target_model:
            raise RuntimeError('No active model configured')
        if self._loaded_model_id == target_model and self._model is not None:
            return target_model, 0
        warm_ms = await asyncio.to_thread(self._load_model_sync, target_model)
        return target_model, warm_ms

    async def render_wav(self, request: SpeechRequest) -> tuple[bytes, int]:
        wavs, sample_rate = await asyncio.to_thread(self._generate_sync, request)
        wav = wavs[0]
        wav_bytes = self._audio_array_to_wav_bytes(wav, sample_rate)
        duration_ms = int(round(len(wav) / sample_rate * 1000)) if sample_rate else self.duration_ms(request.input or '')
        self.sample_rate = sample_rate or self.sample_rate
        return wav_bytes, duration_ms

    async def stream_pcm(self, request: SpeechRequest, chunk_ms: int = 120):
        text = request.input or ''
        sentences = _split_sentences(text)
        if not sentences:
            return

        for sentence in sentences:
            sentence_request = request.model_copy(update={'input': sentence})
            wav_bytes, _ = await self.render_wav(sentence_request)
            pcm = self.wav_to_pcm(wav_bytes)
            chunk_size = max(2, int(self.sample_rate * chunk_ms / 1000) * 2)
            for index in range(0, len(pcm), chunk_size):
                if index == 0:
                    await asyncio.sleep(0)
                yield pcm[index : index + chunk_size]

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
                
                # Attempt to compile the underlying language model or the core auto model
                if hasattr(model, 'model'):
                    model.model = self._torch.compile(model.model, mode='reduce-overhead')
                elif hasattr(model, 'llm'):
                    model.llm = self._torch.compile(model.llm, mode='reduce-overhead')
                else:
                    model = self._torch.compile(model, mode='reduce-overhead')
                print("Model compiled with torch.compile for better CUDA throughput.", flush=True)
            except Exception as e:
                print(f"Warning: Failed to compile model: {e}")

        self._release_model()
        self._model = model
        self._loaded_model_id = model_id
        self.settings.active_model = model_id

        if getattr(self.settings, 'warmup_on_startup', True):
            self._run_warmup_inference(model_id)

        return int((time.perf_counter() - start) * 1000)

    def _run_warmup_inference(self, model_id: str) -> None:
        """Run a short dummy synthesis to prime torch.compile Triton kernels and GPU caches."""
        import logging as _logging
        _log = _logging.getLogger('qwen_tts_server.runtime')
        _log.info('warmup model_id=%s starting dummy inference to prime CUDA kernels...', model_id)
        try:
            task_type = self._task_type_from_model(model_id)
            if task_type == TaskType.voice_design:
                self._model.generate_voice_design(
                    text='Warmup.',
                    language='English',
                    instruct='',
                )
            elif task_type == TaskType.base:
                # base requires ref_audio – skip warmup silently for base models
                _log.info('warmup skipped for Base model (requires ref_audio)')
                return
            else:
                self._model.generate_custom_voice(
                    text='Warmup.',
                    language='English',
                    speaker=self.settings.default_voice,
                    instruct='',
                )
            _log.info('warmup model_id=%s done — CUDA kernels are primed.', model_id)
        except Exception as exc:
            _log.warning('warmup failed (non-critical): %s', exc)

    def _load_runtime_dependencies(self) -> tuple[Any, Any]:
        try:
            import numpy
            import soundfile
            import torch
            from qwen_tts import Qwen3TTSModel
        except Exception as exc:
            raise RuntimeError(
                'Qwen runtime dependencies are missing. Install PyTorch CUDA plus qwen-tts, numpy, and soundfile.'
            ) from exc

        if self.settings.preferred_device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('No CUDA-capable NVIDIA GPU is available for the configured runtime.')

        if torch.cuda.is_available() and self.settings.preferred_device.startswith('cuda'):
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.set_float32_matmul_precision("high")

        self._torch = torch
        self._soundfile = soundfile
        self._numpy = numpy
        return torch, Qwen3TTSModel

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

    def _generate_sync(self, request: SpeechRequest) -> tuple[list[Any], int]:
        if self._model is None:
            raise RuntimeError('No model loaded. Call ensure_model before generation.')
        text = request.input or ''
        if not text.strip():
            raise RuntimeError('Missing input text')

        task_type = request.task_type or self._task_type_from_model(self._loaded_model_id or self.settings.active_model)
        language = request.language or 'Auto'

        with self._torch.inference_mode():
            if task_type == TaskType.voice_design:
                wavs, sample_rate = self._model.generate_voice_design(
                    text=text,
                    language=language,
                    instruct=request.instructions or '',
                )
                return list(wavs), int(sample_rate)

            if task_type == TaskType.base:
                clone_kwargs = self._clone_kwargs(request)
                wavs, sample_rate = self._model.generate_voice_clone(
                    text=text,
                    language=language,
                    **clone_kwargs,
                )
                return list(wavs), int(sample_rate)

            wavs, sample_rate = self._model.generate_custom_voice(
                text=text,
                language=language,
                speaker=request.voice or self.settings.default_voice,
                instruct=request.instructions or '',
            )
            return list(wavs), int(sample_rate)

    def _task_type_from_model(self, model_id: str | None) -> TaskType:
        if not model_id:
            return TaskType.custom_voice
        if model_id.endswith('VoiceDesign'):
            return TaskType.voice_design
        if model_id.endswith('Base'):
            return TaskType.base
        return TaskType.custom_voice

    def _clone_kwargs(self, request: SpeechRequest) -> dict[str, Any]:
        voice_profile = self._resolve_voice_profile(request.voice)
        if voice_profile and voice_profile.audio_bytes:
            ref_audio = self._audio_bytes_to_prompt_audio(voice_profile)
            return {
                'ref_audio': ref_audio,
                'ref_text': request.ref_text or voice_profile.ref_text,
                'x_vector_only_mode': request.x_vector_only_mode,
            }

        if request.ref_audio:
            kwargs: dict[str, Any] = {
                'ref_audio': request.ref_audio,
                'x_vector_only_mode': request.x_vector_only_mode,
            }
            if request.ref_text:
                kwargs['ref_text'] = request.ref_text
            return kwargs

        raise RuntimeError('Base voice cloning requires a saved voice profile or ref_audio + ref_text.')

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
        audio, sample_rate = self._soundfile.read(buffer, dtype='float32')
        return audio, int(sample_rate)

    def _audio_array_to_wav_bytes(self, audio: Any, sample_rate: int) -> bytes:
        if self._numpy is None or self._soundfile is None:
            self._load_runtime_dependencies()
        array = audio
        if hasattr(array, 'detach'):
            array = array.detach().float().cpu().numpy()
        else:
            array = self._numpy.asarray(array)
        output = io.BytesIO()
        self._soundfile.write(output, array, sample_rate, format='WAV')
        return output.getvalue()


def build_synthesizer(settings: Settings, store: InMemoryStore) -> Any:
    if settings.runtime_backend.lower() == 'mock':
        from .services import MockSynthesizer

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
