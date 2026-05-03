# Qwen TTS Server Spec for Genesis

This document defines the HTTP contract and the product shape for a Qwen-based text-to-speech server that should fit the current Genesis runtime while also supporting a richer local UI, queueing, auth, statistics, and benchmarking.

## 1. Scope

Genesis currently sends spoken updates through a local backend proxy at `GET /api/tts` on `server.js`.

For the `qwen3` provider, the runtime already passes these values:

- `provider=qwen3`
- `text=<spoken text>`
- `model=<optional>`
- `voice=<optional>`
- `url=<configured QWEN3-TTS base URL>`

The qwen TTS server should therefore behave like a local audio generation service that accepts text and returns playable audio bytes.

At the same time, the standalone server may expose additional endpoints for:

- queue and job state
- live status and metrics
- model and voice management
- API key management
- benchmarking and parameter comparison

## 2. Compatibility Goal

The compatibility baseline for Genesis remains intentionally simple:

- binary audio response, not JSON
- one request in, one audio stream out
- stable and simple HTTP contract
- easy to proxy from `server.js`

Advanced orchestration is additive. If there is ever a conflict between cleverness and compatibility, compatibility wins.

The Genesis-style standalone surface should now look and behave like a private control room:

- landing page on `/`
- admin UI on `/admin`
- admin access via `X-Admin-Key`
- warm, dark visual language aligned with the shared Genesis styleguide

## 3. Platform and Runtime Assumptions

- Primary target: native Windows
- Secondary target: Linux-compatible architecture
- Preferred runtime: Python `3.12`
- Preferred accelerator: NVIDIA GPU on `cuda:0`
- Exactly one model should be warm on the GPU at a time

The server should be Windows-first but should avoid unnecessary Windows-only design choices.

## 4. Streaming Reality and Risk

Low latency is a top priority, but the implementation must be honest about what is guaranteed.

- `POST /v1/audio/speech` with `stream=true` is the preferred low-latency API shape.
- Streaming should mean progressive PCM chunks when the runtime can produce them.
- Incremental text-input streaming over WebSocket is optional and should not be treated as a hard v1 requirement.
- If a given backend can only simulate or delay streaming, the UI and metrics must reflect that truthfully.

For this project, "fast" should be measured, not assumed.

## 5. Core API

### 5.1 Health check

`GET /health`

Response:

```json
{ "ok": true }
```

Use this only for readiness checks.

### 5.2 Speech synthesis

`POST /v1/audio/speech`

This is the primary synthesis endpoint and should stay close to the OpenAI-style and Qwen/vLLM-style request shape.

#### Request body

```json
{
  "input": "Bitte warte einen Moment.",
  "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "voice": "Ryan",
  "task_type": "CustomVoice",
  "language": "German",
  "instructions": "Speak calmly and clearly.",
  "response_format": "wav",
  "speed": 1.0,
  "stream": false,
  "ref_audio": null,
  "ref_text": null,
  "x_vector_only_mode": false
}
```

#### Field rules

- `input` is required.
- `model` is optional for compatibility, but the standalone server should support explicit model selection.
- `voice` is used for built-in or uploaded voices where applicable.
- `task_type` is optional but recommended. Supported values:
  - `CustomVoice`
  - `VoiceDesign`
  - `Base`
- `language` is optional. `Auto` may be accepted.
- `instructions` is optional and used for mood, expression, tone, or voice design prompts.
- `response_format` is optional. Recommended values:
  - `mp3`
  - `wav`
  - `pcm`
- `speed` is optional.
- `stream` is optional and defaults to `false`.
- `ref_audio` is optional and only relevant for cloning with `Base`.
- `ref_text` is optional when `x_vector_only_mode=true`; otherwise it is expected for higher-quality cloning.
- `x_vector_only_mode` is optional and defaults to `false`.

#### Streaming rules

- `stream=true` requires `response_format="pcm"`.
- `stream=true` should return raw 16-bit signed PCM, 24 kHz mono.
- `speed` adjustment is not required to work in streaming mode and may be rejected.

#### Success response

- `200 OK`
- `Content-Type`:
  - `audio/mpeg` for `mp3`
  - `audio/wav` for `wav`
  - `audio/pcm` for streamed PCM
- body contains raw audio bytes

The browser side in Genesis downloads the non-streaming response as a blob and plays it through the HTML audio element, so the payload must be directly playable.

### 5.3 Optional alternate compatibility mode

If needed, the server may also support:

`GET /tts?text=...&model=...&voice=...`

But `POST /v1/audio/speech` is strongly preferred.

## 6. Queue and Job API

The standalone server should expose a queue-aware orchestration layer so clients and the web UI can inspect state instead of guessing.

### 6.1 Create job

`POST /v1/jobs`

Creates a synthesis job and returns immediately.

Example response:

```json
{
  "job_id": "job_01HV...",
  "status": "queued",
  "queue_position": 2,
  "eta_ms": 4300
}
```

### 6.2 List jobs

`GET /v1/jobs`

Should support basic filtering such as:

- `status`
- `limit`
- `cursor`

### 6.3 Get job

`GET /v1/jobs/{id}`

Example response:

```json
{
  "job_id": "job_01HV...",
  "status": "running",
  "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "task_type": "CustomVoice",
  "voice": "Ryan",
  "input_preview": "Bitte warte einen Moment.",
  "queue_position": 0,
  "eta_ms": 1200,
  "metrics": {
    "queue_wait_ms": 840,
    "ttfa_ms": 190,
    "audio_duration_ms": 2100,
    "realtime_x": 4.2
  }
}
```

### 6.4 Cancel job

`DELETE /v1/jobs/{id}`

Allowed while `queued` or `warming`. Cancellation during active synthesis may be best-effort.

### 6.5 Download job audio

`GET /v1/jobs/{id}/audio`

Returns the finished audio artifact if retained.

### 6.6 Job states

The server should use a small, understandable set of states:

- `queued`
- `warming`
- `running`
- `streaming`
- `completed`
- `failed`
- `cancelled`

## 7. Live Status and Metrics API

### 7.1 Server events

`GET /v1/events`

This should use Server-Sent Events for:

- `job.updated`
- `queue.updated`
- `stats.updated`
- `model.updated`
- `benchmark.updated`

SSE is the preferred status channel for v1 because it is simpler than WebSocket and good enough for queue, progress, ETA, and dashboard updates.

### 7.2 Summary stats

`GET /v1/stats`

Example response:

```json
{
  "active_model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "queue_depth": 1,
  "worker_state": "running",
  "rolling": {
    "ttfa_ms_avg": 205,
    "queue_wait_ms_avg": 510,
    "job_wall_ms_avg": 1180,
    "realtime_x_avg": 3.8
  },
  "global": {
    "jobs_total": 184,
    "audio_seconds_total": 912.4,
    "realtime_x_avg": 3.4
  }
}
```

### 7.3 GPU stats

`GET /v1/stats/gpu`

Should expose lightweight operational values such as:

- GPU name
- memory used
- memory total
- utilization
- temperature if available

## 8. Voice and Model API

### 8.1 List voices

`GET /v1/audio/voices`

Should list:

- built-in voices for the loaded model
- stored custom voice profiles, if any

### 8.2 Create voice profile

`POST /v1/audio/voices`

Recommended as multipart form upload.

Suggested fields:

- `audio_sample`
- `name`
- `consent`
- `ref_text` optional

This endpoint should create a reusable stored voice profile for later synthesis calls.

### 8.3 Delete voice profile

`DELETE /v1/audio/voices/{id}`

### 8.4 List models

`GET /v1/models`

Should return known supported models and indicate:

- current active model
- whether a model is loaded
- supported task types

## 9. API Key and Security API

`GET /health` should remain open.

`GET /api/health` should also remain open for the landing page and quick readiness checks.

The private dashboard should use an admin key rather than a user-facing client key.

Preferred auth format:

- `X-Admin-Key: <key>`

Compatibility formats that may still be accepted:

- `Authorization: Bearer <key>`
- `X-API-Key: <key>`

Recommended admin endpoints:

- `GET /api/admin/keys`
- `POST /api/admin/keys`
- `GET /api/admin/settings`
- `PUT /api/admin/settings`

Keys should be stored hashed, not in plaintext.

## 10. Whisper Integration

The web UI should support creating or refining clone profiles with help from an external Whisper server.

### 10.1 Proxy transcription helper

`POST /v1/tools/transcribe`

The server should forward the uploaded audio to the configured Whisper server using:

- `POST`
- `multipart/form-data`
- `file=<audio>`
- `engine=lokal`
- `voice_ident=true`

Expected Whisper response shape:

```json
{
  "transcription": "Hallo, das ist ein Test.",
  "voice_vector": [0.12, -0.45, 0.88]
}
```

The TTS UI should allow the transcription to be edited before a clone profile is saved.

## 11. Benchmark API

The UI should include a dedicated benchmark tab so parameters can be compared quickly and repeatably.

Benchmarking must be treated as a first-class feature, not an improvised manual workflow.

### 11.1 Goals

The benchmark feature should make it easy to compare:

- model variants
- task types
- built-in voices
- instruction styles
- response format
- streaming vs non-streaming
- short, medium, and long texts
- warm vs cold model situations where feasible

### 11.2 Benchmark run creation

`POST /v1/benchmarks/runs`

Example request:

```json
{
  "name": "0.6B vs 1.7B German short-form",
  "dataset": "de_standard_v1",
  "iterations": 5,
  "warmup_iterations": 1,
  "cooldown_ms": 1000,
  "exclusive": true,
  "cases": [
    {
      "label": "0.6B Ryan neutral",
      "request": {
        "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "task_type": "CustomVoice",
        "voice": "Ryan",
        "language": "German",
        "instructions": "",
        "response_format": "wav",
        "stream": false
      }
    },
    {
      "label": "1.7B Ryan expressive",
      "request": {
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "task_type": "CustomVoice",
        "voice": "Ryan",
        "language": "German",
        "instructions": "Sound energetic but controlled.",
        "response_format": "wav",
        "stream": false
      }
    }
  ]
}
```

### 11.3 Benchmark result endpoints

- `GET /v1/benchmarks/runs`
- `GET /v1/benchmarks/runs/{id}`
- `DELETE /v1/benchmarks/runs/{id}`

### 11.4 Benchmark execution rules

To keep benchmarks reliable:

- benchmark cases should run serially by default
- warmup iterations should be excluded from final comparison metrics
- the run should record whether the model was already warm or had to be loaded
- benchmark runs should support `exclusive=true` so they can temporarily own the worker
- queue wait caused by unrelated user jobs should not pollute benchmark timing
- each case should keep the exact input payload used for the run

### 11.5 Metrics that must be captured

Per case and per iteration:

- `queue_wait_ms`
- `model_warm_ms`
- `ttfa_ms`
- `job_wall_ms`
- `audio_duration_ms`
- `realtime_x`
- `output_bytes`
- `success`
- `error_message` if failed

Aggregated per case:

- mean
- median
- min
- max
- standard deviation

### 11.6 Stored benchmark artifacts

The server should store:

- benchmark definition
- raw iteration results
- aggregate summary
- server and GPU info at run time
- active backend info

Audio artifacts for benchmarks may be retained for a shorter period than normal jobs, but the metadata should remain.

## 12. Web UI Requirements

The server should ship with a React-based UI for local inspection and operations.

### 12.1 Main tabs

- `Command`
- `Queue`
- `Synthesis`
- `Voice Lab`
- `Benchmarks`
- `Access`
- `Runtime`

### 12.2 Benchmark tab

The benchmark tab should support:

- selecting one or more predefined text datasets
- entering custom benchmark texts
- choosing multiple parameter sets side by side
- setting warmup count and measured iteration count
- enabling exclusive benchmark mode
- starting, stopping, and reviewing runs
- showing a summary table across cases
- showing run-to-run variance, not just averages
- charting:
  - TTFA
  - total wall time
  - realtime factor
  - queue wait
  - warmup vs measured runs
- drilling into each single iteration
- reusing a previous benchmark config as a template

The benchmark tab should make it obvious whether a result reflects:

- warm model vs cold model
- streaming vs non-streaming
- queue-free vs queue-contended execution

## 13. Minimum Behavior Required by Genesis

The server must still:

- accept plain text input
- return valid audio bytes
- keep the success path non-JSON
- handle UTF-8 text correctly
- work with German text
- allow long paragraphs
- return an error status code on failure

## 14. Error Handling

Use normal HTTP status codes:

- `400` for missing or invalid input
- `401` for missing API key
- `403` for invalid or disabled API key
- `404` for unknown job, model, voice, key, or benchmark run
- `409` for incompatible worker state, such as trying to start an exclusive benchmark during another exclusive task
- `413` for oversized text or upload
- `415` for unsupported format requests
- `429` for queue saturation
- `500` for synthesis failures

Error responses may be JSON or plain text, but the success path for synthesis must be audio bytes.

## 15. Recommended Audio Format

Preferred order for non-streaming:

1. `audio/mpeg`
2. `audio/wav`
3. `audio/ogg`

Preferred format for streaming:

1. raw `pcm16`, 24 kHz mono

## 16. Text Handling Rules

The Genesis TTS prompt expects the spoken output to be:

- short and natural
- same language as the assistant response
- clear with symbols, URLs, filenames, and numbers spoken in a human-friendly way

The TTS server itself does not need to rewrite the text, but it should preserve Unicode and punctuation faithfully unless the model requires preprocessing.

## 17. Suggested Genesis Backend Adapter Contract

When `server.js` is extended for `qwen3`, it should:

- read the configured base URL from `qwen3Url`
- send the `text` field from `/api/tts`
- pass through optional `model` and `voice`
- optionally pass through `task_type` and `instructions`
- stream the audio response back to the browser when enabled

That means the qwen server should be usable as a drop-in local service behind a thin proxy while still allowing richer standalone usage.

## 18. Example Requests

### 18.1 Basic synthesis

```bash
curl -X POST http://127.0.0.1:8088/v1/audio/speech \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{
    "input": "Bitte warte einen Moment.",
    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "voice": "Ryan",
    "task_type": "CustomVoice",
    "response_format": "mp3"
  }' --output out.mp3
```

### 18.2 PCM streaming

```bash
curl -X POST http://127.0.0.1:8088/v1/audio/speech \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{
    "input": "Bitte warte einen Moment.",
    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "voice": "Ryan",
    "task_type": "CustomVoice",
    "response_format": "pcm",
    "stream": true
  }' --no-buffer > out.pcm
```

### 18.3 Benchmark run

```bash
curl -X POST http://127.0.0.1:8088/v1/benchmarks/runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{
    "name": "German smoke benchmark",
    "dataset": "de_standard_v1",
    "iterations": 3,
    "warmup_iterations": 1,
    "exclusive": true,
    "cases": [
      {
        "label": "0.6B custom",
        "request": {
          "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
          "task_type": "CustomVoice",
          "voice": "Ryan",
          "language": "German",
          "response_format": "wav",
          "stream": false
        }
      }
    ]
  }'
```

## 19. Implementation Notes for the New Agent

Build the server so it is boring in the best possible way:

- one backend service
- one queue-aware synthesis worker
- one React dashboard
- no chat logic
- no agent orchestration logic
- no hidden state except the operational state needed for performance, queueing, voices, metrics, and benchmarks

The benchmark feature is part of the product, not a later afterthought.
