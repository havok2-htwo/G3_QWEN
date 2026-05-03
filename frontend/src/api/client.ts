import type {
  ApiKeyRecord,
  ComposeRequest,
  DashboardClient,
  DashboardClientConfig,
  JobMetrics,
  JobRecord,
  ModelInfo,
  ResponseFormat,
  ServerSettings,
  Snapshot,
  TaskType,
  VoiceProfile
} from '../types';
import { createMockDashboardClient } from './mock';

const defaultSnapshot: Snapshot = {
  overview: {
    activeModel: 'Qwen3-TTS-12Hz-0.6B-CustomVoice',
    queueDepth: 0,
    workerState: 'idle',
    ttfaMsAvg: 0,
    queueWaitMsAvg: 0,
    jobWallMsAvg: 0,
    realtimeXAvg: 0,
    jobsTotal: 0,
    audioSecondsTotal: 0,
    gpuMemoryUsedMb: 0,
    gpuMemoryTotalMb: 0,
    gpuUtilizationPct: 0,
    gpuTemperatureC: 0
  },
  models: [],
  jobs: [],
  voices: [],
  keys: [],
  settings: {
    mode: 'mock',
    baseUrl: 'http://127.0.0.1:8088',
    modelDirectory: 'x:/dev/G3_QWEN_TTS/models',
    defaultModel: 'Qwen3-TTS-12Hz-0.6B-CustomVoice',
    defaultVoice: 'Ryan',
    whisperBaseUrl: 'http://127.0.0.1:9000',
    whisperPath: '/transcribe/',
    retentionDays: 7,
    queueLimit: 32,
    runtimeBackend: 'qwen',
    allowModelDownloads: false,
    preferredDevice: 'cuda:0',
    attentionImplementation: 'sdpa',
    torchDtype: 'bfloat16',
    sampleRate: 24000,
    pollIntervalMs: 1000,
    theme: 'onyx',
    builtInVoices: ['Ryan', 'Vivian', 'Serena', 'Aiden', 'Mia', 'Nova', 'Ava'],
    sentenceChunking: true,
    shortSentenceMergeMaxChars: 30,
    followingSentenceMergeMinChars: 20,
    maxParallelRequests: 6,
    maxBatchSize: 8,
    batchWaitMs: 35,
    streamChunkMs: 140,
    streamPrebufferMs: 0
  },
  transcriptionHint: 'Upload a sample and let Whisper draft the reference text.'
};

interface BackendStatsResponse {
  active_model: string;
  queue_depth: number;
  worker_state: string;
  rolling: {
    ttfa_ms_avg?: number | null;
    queue_wait_ms_avg?: number | null;
    job_wall_ms_avg?: number | null;
    realtime_x_avg?: number | null;
  };
  global: {
    jobs_total: number;
    audio_seconds_total: number;
    realtime_x_avg?: number | null;
  };
}

interface BackendGpuStatsResponse {
  name: string;
  memory_used_mb: number;
  memory_total_mb: number;
  utilization_percent: number;
  temperature_c?: number | null;
}

interface BackendJobListItem {
  job_id: string;
  status: string;
  model?: string | null;
  task_type?: string | null;
  voice?: string | null;
  input_preview: string;
  queue_position: number;
  eta_ms: number;
  created_at: string;
  updated_at: string;
  metrics?: {
    queue_wait_ms?: number | null;
    ttfa_ms?: number | null;
    audio_duration_ms?: number | null;
    job_wall_ms?: number | null;
    realtime_x?: number | null;
  };
  error_message?: string | null;
}

interface BackendJobDetailResponse extends BackendJobListItem {}

interface BackendVoiceResponse {
  voice_id: string;
  name: string;
  source: string;
  created_at?: string | null;
}

interface BackendKeyResponse {
  key_id: string;
  name: string;
  created_at: string;
  last_used_at?: string | null;
  disabled: boolean;
}

interface BackendModelResponse {
  model_id: string;
  loaded: boolean;
  active: boolean;
  task_types: string[];
}

interface BackendMetricSummary {
  mean?: number | null;
  minimum?: number | null;
  maximum?: number | null;
}




interface BackendSpeechJobCreateResponse {
  job_id: string;
}

interface BackendSettingsResponse {
  mode?: string | null;
  baseUrl?: string | null;
  base_url?: string | null;
  modelDirectory?: string | null;
  model_directory?: string | null;
  defaultModel?: string | null;
  default_model?: string | null;
  defaultVoice?: string | null;
  default_voice?: string | null;
  whisperBaseUrl?: string | null;
  whisper_base_url?: string | null;
  whisperPath?: string | null;
  whisper_path?: string | null;
  retentionDays?: number | string | null;
  retention_days?: number | string | null;
  queueLimit?: number | string | null;
  queue_limit?: number | string | null;
  runtimeBackend?: string | null;
  runtime_backend?: string | null;
  allowModelDownloads?: boolean | null;
  allow_model_downloads?: boolean | null;
  preferredDevice?: string | null;
  preferred_device?: string | null;
  attentionImplementation?: string | null;
  attention_implementation?: string | null;
  torchDtype?: string | null;
  torch_dtype?: string | null;
  sampleRate?: number | string | null;
  sample_rate?: number | string | null;
  pollIntervalMs?: number | string | null;
  poll_interval_ms?: number | string | null;
  theme?: 'ember' | 'onyx' | string | null;
  built_in_voices?: string[] | null;
  sentence_chunking?: boolean | null;
  short_sentence_merge_max_chars?: number | string | null;
  following_sentence_merge_min_chars?: number | string | null;
  max_parallel_requests?: number | string | null;
  max_batch_size?: number | string | null;
  batch_wait_ms?: number | string | null;
  stream_chunk_ms?: number | string | null;
  stream_prebuffer_ms?: number | string | null;
}

interface BackendAdminKeyMeta {
  key_id: string;
  label: string;
  created_at: string;
  last_used_at?: string | null;
}

interface BackendAdminKeyResponse {
  admin_key: BackendAdminKeyMeta;
}

interface BackendAdminKeyRotateResponse extends BackendAdminKeyResponse {
  token: string;
}

interface SafeFetchResult<T> {
  label: string;
  url: string;
  data: T;
  ok: boolean;
  error?: string;
  status?: number;
}

function pickString(...values: Array<string | null | undefined>) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return '';
}

function pickNumber(...values: Array<number | string | null | undefined>) {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return 0;
}

function mapServerSettings(response: BackendSettingsResponse, config: DashboardClientConfig): ServerSettings {
  const whisperBaseUrl =
    response.whisperBaseUrl === null || response.whisper_base_url === null
      ? ''
      : typeof response.whisperBaseUrl === 'string'
        ? response.whisperBaseUrl
        : typeof response.whisper_base_url === 'string'
          ? response.whisper_base_url
          : defaultSnapshot.settings.whisperBaseUrl;

  return {
    mode: config.mode,
    baseUrl: config.baseUrl,
    modelDirectory: pickString(response.modelDirectory, response.model_directory) || defaultSnapshot.settings.modelDirectory,
    defaultModel: pickString(response.defaultModel, response.default_model) || defaultSnapshot.settings.defaultModel,
    defaultVoice: pickString(response.defaultVoice, response.default_voice) || defaultSnapshot.settings.defaultVoice,
    whisperBaseUrl,
    whisperPath: pickString(response.whisperPath, response.whisper_path) || defaultSnapshot.settings.whisperPath,
    retentionDays: pickNumber(response.retentionDays, response.retention_days) || defaultSnapshot.settings.retentionDays,
    queueLimit: pickNumber(response.queueLimit, response.queue_limit) || defaultSnapshot.settings.queueLimit,
    runtimeBackend: pickString(response.runtimeBackend, response.runtime_backend) || defaultSnapshot.settings.runtimeBackend,
    allowModelDownloads:
      response.allowModelDownloads ??
      response.allow_model_downloads ??
      defaultSnapshot.settings.allowModelDownloads,
    preferredDevice: pickString(response.preferredDevice, response.preferred_device) || defaultSnapshot.settings.preferredDevice,
    attentionImplementation:
      pickString(response.attentionImplementation, response.attention_implementation) ||
      defaultSnapshot.settings.attentionImplementation,
    torchDtype: pickString(response.torchDtype, response.torch_dtype) || defaultSnapshot.settings.torchDtype,
    sampleRate: pickNumber(response.sampleRate, response.sample_rate) || defaultSnapshot.settings.sampleRate,
    pollIntervalMs: pickNumber(response.pollIntervalMs, response.poll_interval_ms) || defaultSnapshot.settings.pollIntervalMs,
    theme: response.theme === 'ember' ? 'ember' : 'onyx',
    builtInVoices: Array.isArray(response.built_in_voices) ? response.built_in_voices : defaultSnapshot.settings.builtInVoices,
    sentenceChunking: response.sentence_chunking ?? defaultSnapshot.settings.sentenceChunking,
    shortSentenceMergeMaxChars:
      pickNumber(response.short_sentence_merge_max_chars) || defaultSnapshot.settings.shortSentenceMergeMaxChars,
    followingSentenceMergeMinChars:
      pickNumber(response.following_sentence_merge_min_chars) || defaultSnapshot.settings.followingSentenceMergeMinChars,
    maxParallelRequests: pickNumber(response.max_parallel_requests) || defaultSnapshot.settings.maxParallelRequests,
    maxBatchSize: pickNumber(response.max_batch_size) || defaultSnapshot.settings.maxBatchSize,
    batchWaitMs: pickNumber(response.batch_wait_ms) || defaultSnapshot.settings.batchWaitMs,
    streamChunkMs: pickNumber(response.stream_chunk_ms) || defaultSnapshot.settings.streamChunkMs,
    streamPrebufferMs: pickNumber(response.stream_prebuffer_ms) || defaultSnapshot.settings.streamPrebufferMs
  };
}

function toSettingsPayload(settings: ServerSettings) {
  return {
    model_directory: settings.modelDirectory,
    default_model: settings.defaultModel,
    default_voice: settings.defaultVoice,
    whisper_base_url: settings.whisperBaseUrl,
    whisper_path: settings.whisperPath,
    retention_days: settings.retentionDays,
    queue_limit: settings.queueLimit,
    allow_model_downloads: settings.allowModelDownloads,
    preferred_device: settings.preferredDevice,
    attention_implementation: settings.attentionImplementation,
    torch_dtype: settings.torchDtype,
    poll_interval_ms: settings.pollIntervalMs,
    theme: settings.theme,
    sentence_chunking: settings.sentenceChunking,
    short_sentence_merge_max_chars: settings.shortSentenceMergeMaxChars,
    following_sentence_merge_min_chars: settings.followingSentenceMergeMinChars,
    max_parallel_requests: settings.maxParallelRequests,
    max_batch_size: settings.maxBatchSize,
    batch_wait_ms: settings.batchWaitMs,
    stream_chunk_ms: settings.streamChunkMs,
    stream_prebuffer_ms: settings.streamPrebufferMs
  };
}

function normalizeBaseUrl(baseUrl: string) {
  return baseUrl.replace(/\/$/, '');
}

function headers(adminKey: string): HeadersInit {
  const result: Record<string, string> = {};
  if (adminKey.trim()) {
    result['X-Admin-Key'] = adminKey.trim();
  }
  return result;
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    if (text) {
      try {
        const payload = JSON.parse(text) as { detail?: unknown };
        if (typeof payload.detail === 'string' && payload.detail.trim()) {
          detail = payload.detail;
        }
      } catch {
        // Keep the raw response text when the payload is not JSON.
      }
    }
    const error = new Error(detail || `Request failed with status ${response.status}`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return (await response.json()) as T;
}

function toTaskType(value: string | null | undefined): TaskType {
  if (value === 'VoiceDesign') {
    return 'VoiceDesign';
  }
  if (value === 'Base') {
    return 'Base';
  }
  return 'CustomVoice';
}

function toResponseFormat(value: string | null | undefined): ResponseFormat {
  if (value === 'mp3' || value === 'pcm') {
    return value;
  }
  return 'wav';
}

function progressFromStatus(status: string): number {
  switch (status) {
    case 'queued':
      return 0;
    case 'warming':
      return 12;
    case 'running':
      return 48;
    case 'streaming':
      return 72;
    case 'cancelling':
      return 88;
    case 'completed':
    case 'failed':
    case 'cancelled':
      return 100;
    default:
      return 0;
  }
}

function modelLabel(modelId: string): string {
  const raw = modelId.split('/').pop() ?? modelId;
  return raw.replace('Qwen3-TTS-12Hz-', '').replace(/-/g, ' ');
}

function mapJobMetrics(metrics?: BackendJobListItem['metrics']): JobMetrics | undefined {
  if (!metrics) {
    return undefined;
  }
  return {
    queueWaitMs: Number(metrics.queue_wait_ms ?? 0),
    ttfaMs: Number(metrics.ttfa_ms ?? 0),
    audioDurationMs: Number(metrics.audio_duration_ms ?? 0),
    jobWallMs: Number(metrics.job_wall_ms ?? 0),
    realtimeX: Number(metrics.realtime_x ?? 0)
  };
}

function mapJob(item: BackendJobListItem | BackendJobDetailResponse, audioBaseUrl = defaultSnapshot.settings.baseUrl): JobRecord {
  return {
    id: item.job_id,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    status: item.status as JobRecord['status'],
    model: item.model ?? defaultSnapshot.settings.defaultModel,
    taskType: toTaskType(item.task_type),
    voice: item.voice ?? defaultSnapshot.settings.defaultVoice,
    language: 'Auto',
    input: item.input_preview,
    instructions: '',
    responseFormat: item.status === 'streaming' ? 'pcm' : 'wav',
    stream: item.status === 'streaming',
    queuePosition: item.queue_position,
    etaMs: item.eta_ms,
    progress: progressFromStatus(item.status),
    metrics: mapJobMetrics('metrics' in item ? item.metrics : undefined),
    errorMessage: 'error_message' in item ? item.error_message ?? undefined : undefined,
    audioUrl: item.status === 'completed' ? `${audioBaseUrl}/v1/jobs/${item.job_id}/audio` : undefined
  };
}

function mapVoice(item: BackendVoiceResponse): VoiceProfile {
  return {
    id: item.voice_id,
    name: item.name,
    kind: item.source === 'custom' ? 'custom' : 'built-in',
    model: defaultSnapshot.settings.defaultModel,
    language: 'Auto',
    style: item.source === 'custom' ? 'Custom voice profile' : 'Built-in voice',
    sampleLabel: item.source === 'custom' ? 'Uploaded sample' : 'Built-in voice',
    consent: item.source !== 'custom'
  };
}

function mapKey(item: BackendKeyResponse): ApiKeyRecord {
  return {
    id: item.key_id,
    name: item.name,
    status: item.disabled ? 'disabled' : 'active',
    lastUsedAt: item.last_used_at ?? item.created_at,
    createdAt: item.created_at,
    tokenPreview: `${item.key_id.slice(0, 12)}...`
  };
}

function mapModel(item: BackendModelResponse): ModelInfo {
  return {
    id: item.model_id,
    label: modelLabel(item.model_id),
    loaded: item.loaded,
    taskTypes: item.task_types.map((entry) => toTaskType(entry)),
    status: item.active ? 'warm' : item.loaded ? 'warming' : 'cold'
  };
}

function metricSummary(input?: BackendMetricSummary) {
  return {
    avg: Number(input?.mean ?? 0),
    min: Number(input?.minimum ?? 0),
    max: Number(input?.maximum ?? 0)
  };
}

function mapOverview(stats: BackendStatsResponse, gpu?: BackendGpuStatsResponse): Snapshot['overview'] {
  return {
    activeModel: stats.active_model,
    queueDepth: stats.queue_depth,
    workerState: stats.worker_state,
    ttfaMsAvg: Number(stats.rolling.ttfa_ms_avg ?? 0),
    queueWaitMsAvg: Number(stats.rolling.queue_wait_ms_avg ?? 0),
    jobWallMsAvg: Number(stats.rolling.job_wall_ms_avg ?? 0),
    realtimeXAvg: Number(stats.rolling.realtime_x_avg ?? stats.global.realtime_x_avg ?? 0),
    jobsTotal: Number(stats.global.jobs_total ?? 0),
    audioSecondsTotal: Number(stats.global.audio_seconds_total ?? 0),
    gpuMemoryUsedMb: Number(gpu?.memory_used_mb ?? 0),
    gpuMemoryTotalMb: Number(gpu?.memory_total_mb ?? 0),
    gpuUtilizationPct: Number(gpu?.utilization_percent ?? 0),
    gpuTemperatureC: Number(gpu?.temperature_c ?? 0)
  };
}

function toSpeechPayload(request: {
  input?: string;
  model: string;
  voice: string;
  taskType: TaskType;
  language: string;
  instructions: string;
  responseFormat: ResponseFormat;
  speed: number;
  stream: boolean;
  refText?: string;
  refAudioLabel?: string;
  xVectorOnlyMode?: boolean;
}) {
  return {
    input: request.input,
    model: request.model,
    voice: request.voice,
    task_type: request.taskType,
    language: request.language,
    instructions: request.instructions,
    response_format: request.responseFormat,
    speed: request.speed,
    stream: request.stream,
    ref_audio: request.refAudioLabel || null,
    ref_text: request.refText || null,
    x_vector_only_mode: Boolean(request.xVectorOnlyMode)
  };
}

function createHttpClient(config: DashboardClientConfig): DashboardClient {
  let current = config;

  return {
    mode: 'http',
    updateConfig(nextConfig: DashboardClientConfig) {
      current = nextConfig;
    },
    async verifyAdminKey(adminKey: string) {
      const response = await fetch(`${normalizeBaseUrl(current.baseUrl)}/api/admin/keys`, {
        headers: headers(adminKey),
      }).then((result) => parseJson<BackendAdminKeyResponse>(result));
      return mapAdminKey(response.admin_key, adminKey);
    },
    async rotateAdminKey() {
      const response = await fetch(`${normalizeBaseUrl(current.baseUrl)}/api/admin/keys`, {
        method: 'POST',
        headers: headers(current.adminKey),
      }).then((result) => parseJson<BackendAdminKeyRotateResponse>(result));
      return mapAdminKey(response.admin_key, response.token);
    },
    async getSnapshot() {
      const baseUrl = normalizeBaseUrl(current.baseUrl);
      const h = headers(current.adminKey);

      // Each request is individually wrapped so one failure doesn't block everything.
      // The bootstrap still fails when the essential dashboard endpoints all fail.
      async function safeFetch<T>(url: string, fallback: T, label: string): Promise<SafeFetchResult<T>> {
        const controller = new AbortController();
        let timedOut = false;
        const timeout = window.setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, 15000);

        try {
          const response = await fetch(url, { headers: h, signal: controller.signal });
          return { label, url, data: await parseJson<T>(response), ok: true };
        } catch (err) {
          let message = err instanceof Error ? err.message : String(err);
          const status = typeof err === 'object' && err !== null && 'status' in err ? Number((err as { status?: number }).status) : undefined;
          if (timedOut) {
            message = `${label} timed out after 15000ms (${url})`;
          }
          console.warn(`[Snapshot] ${label} failed, using fallback:`, err);
          return { label, url, data: fallback, ok: false, error: message, status };
        } finally {
          window.clearTimeout(timeout);
        }
      }

      const defaultGpu: BackendGpuStatsResponse = { name: 'N/A', memory_used_mb: 0, memory_total_mb: 0, utilization_percent: 0, temperature_c: 0 };
      const defaultStats: BackendStatsResponse = { active_model: '', queue_depth: 0, worker_state: 'unknown', rolling: {}, global: { jobs_total: 0, audio_seconds_total: 0 } };
      const defaultSettingsResp: BackendSettingsResponse = {};

      const [statsResult, gpuResult, jobsResult, voicesResult, keysResult, modelsResult, settingsResult] = await Promise.all([
        safeFetch<BackendStatsResponse>(`${baseUrl}/v1/stats`, defaultStats, 'stats'),
        safeFetch<BackendGpuStatsResponse>(`${baseUrl}/v1/stats/gpu`, defaultGpu, 'gpu'),
        safeFetch<BackendJobListItem[]>(`${baseUrl}/v1/jobs`, [], 'jobs'),
        safeFetch<BackendVoiceResponse[]>(`${baseUrl}/v1/audio/voices`, [], 'voices'),
        safeFetch<BackendKeyResponse[]>(`${baseUrl}/v1/keys`, [], 'keys'),
        safeFetch<BackendModelResponse[]>(`${baseUrl}/v1/models`, [], 'models'),
        safeFetch<BackendSettingsResponse>(`${baseUrl}/v1/settings`, defaultSettingsResp, 'settings'),
      ]);

      const failures = [statsResult, gpuResult, jobsResult, voicesResult, keysResult, modelsResult, settingsResult].filter((result) => !result.ok);
      if (failures.length > 0) {
        console.warn(
          '[Snapshot] failing endpoints:',
          failures.map((result) => `${result.label}: ${result.error ?? 'unknown error'}`).join(' | ')
        );
      }

      const essentialFailures = [statsResult, modelsResult, settingsResult].filter((result) => !result.ok);
      if (essentialFailures.length === 3) {
        const message = `Backend bootstrap failed: ${essentialFailures.map((result) => `${result.label}: ${result.error ?? 'unknown error'}`).join(' | ')}`;
        const bootstrapError = new Error(message) as Error & { status?: number };
        if (essentialFailures.every((result) => result.status === 401)) {
          bootstrapError.status = 401;
        }
        throw bootstrapError;
      }

      const mappedSettings = mapServerSettings(settingsResult.data, current);

      return {
        overview: mapOverview(statsResult.data, gpuResult.data),
        jobs: jobsResult.data.map((entry) => mapJob(entry, baseUrl)),
        voices: voicesResult.data.map(mapVoice),
        keys: keysResult.data.map(mapKey),
        models: modelsResult.data.map(mapModel),
        settings: {
          ...mappedSettings,
          mode: current.mode,
          baseUrl: current.baseUrl,
        },
        transcriptionHint: defaultSnapshot.transcriptionHint
      };
    },
    async createSpeechJob(request: ComposeRequest) {
      const baseUrl = normalizeBaseUrl(current.baseUrl);
      const create = await fetch(`${baseUrl}/v1/jobs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers(current.adminKey)
        },
        body: JSON.stringify(toSpeechPayload(request))
      }).then((response) => parseJson<BackendSpeechJobCreateResponse>(response));

      const detail = await fetch(`${baseUrl}/v1/jobs/${create.job_id}`, { headers: headers(current.adminKey) }).then((response) => parseJson<BackendJobDetailResponse>(response));
      return mapJob(detail, baseUrl);
    },
    async createVoiceProfile(input) {
      const formData = new FormData();
      formData.append('audio_sample', input.file ?? new Blob([input.sampleLabel || input.sampleFileName], { type: 'audio/wav' }), input.sampleFileName);
      formData.append('name', input.name);
      formData.append('consent', String(input.consent));
      formData.append('ref_text', input.refText);

      const response = await fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/audio/voices`, {
        method: 'POST',
        headers: headers(current.adminKey),
        body: formData
      }).then((result) => parseJson<BackendVoiceResponse>(result));

      return {
        id: response.voice_id,
        name: response.name,
        kind: 'custom',
        model: input.model,
        language: input.language,
        style: input.style,
        sampleLabel: input.sampleFileName,
        consent: input.consent
      };
    },
    async transcribeSample(input: { file: File | null; fileName: string }) {
      const formData = new FormData();
      formData.append('file', input.file ?? new Blob([input.fileName || 'sample'], { type: 'audio/wav' }), input.fileName || 'sample.wav');
      return fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/tools/transcribe`, {
        method: 'POST',
        headers: headers(current.adminKey),
        body: formData
      }).then((response) => parseJson<{ transcription: string; voice_vector?: number[] }>(response)).then((payload) => ({
        transcription: payload.transcription,
        voiceVector: payload.voice_vector ?? []
      }));
    },
    async createApiKey(name: string) {
      const response = await fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/keys`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers(current.adminKey)
        },
        body: JSON.stringify({ name })
      }).then((result) => parseJson<{ key_id: string; name: string; created_at: string; api_key: string }>(result));

      return {
        id: response.key_id,
        name: response.name,
        status: 'active' as const,
        lastUsedAt: '',
        createdAt: response.created_at,
        tokenPreview: `sk-...${String(response.api_key).slice(-4)}`,
        rawKey: response.api_key
      };
    },
    async deleteApiKey(id: string) {
      await fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/keys/${id}`, {
        method: 'DELETE',
        headers: headers(current.adminKey)
      }).then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }
      });
    },
    async cancelJob(id: string) {
      await fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/jobs/${id}`, {
        method: 'DELETE',
        headers: headers(current.adminKey)
      }).then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }
      });
    },
    async getJobAudio(id: string) {
      const response = await fetch(`${normalizeBaseUrl(current.baseUrl)}/v1/jobs/${id}/audio`, {
        headers: headers(current.adminKey)
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed with status ${response.status}`);
      }
      return response.blob();
    },
    async updateSettings(settings: ServerSettings) {
      const baseUrl = normalizeBaseUrl(current.baseUrl);
      const response = await fetch(`${baseUrl}/v1/settings`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...headers(current.adminKey)
        },
        body: JSON.stringify(toSettingsPayload(settings))
      }).then((result) => parseJson<BackendSettingsResponse>(result));

      const mappedSettings = mapServerSettings(response, current);
      return {
        ...settings,
        ...mappedSettings,
        mode: settings.mode,
        baseUrl: settings.baseUrl,
      };
    }
  };
}

export function createDashboardClient(initialConfig: DashboardClientConfig): DashboardClient {
  const mock = createMockDashboardClient(initialConfig);
  const http = createHttpClient(initialConfig);
  let current = initialConfig;

  return {
    get mode() {
      return current.mode;
    },
    updateConfig(config: DashboardClientConfig) {
      current = config;
      mock.updateConfig(config);
      http.updateConfig(config);
    },
    verifyAdminKey(adminKey: string) {
      return current.mode === 'http' ? http.verifyAdminKey(adminKey) : mock.verifyAdminKey(adminKey);
    },
    rotateAdminKey() {
      return current.mode === 'http' ? http.rotateAdminKey() : mock.rotateAdminKey();
    },
    subscribe(listener: () => void) {
      return mock.subscribe?.(listener) ?? (() => undefined);
    },
    getSnapshot() {
      return current.mode === 'http' ? http.getSnapshot() : mock.getSnapshot();
    },
    createSpeechJob(request: ComposeRequest) {
      return current.mode === 'http' ? http.createSpeechJob(request) : mock.createSpeechJob(request);
    },
    createVoiceProfile(input) {
      return current.mode === 'http' ? http.createVoiceProfile(input) : mock.createVoiceProfile(input);
    },
    transcribeSample(input: { file: File | null; fileName: string }) {
      return current.mode === 'http' ? http.transcribeSample(input) : mock.transcribeSample(input);
    },
    createApiKey(name: string) {
      return current.mode === 'http' ? http.createApiKey(name) : mock.createApiKey(name);
    },
    deleteApiKey(id: string) {
      return current.mode === 'http' ? http.deleteApiKey(id) : mock.deleteApiKey(id);
    },
    cancelJob(id: string) {
      return current.mode === 'http' ? http.cancelJob(id) : mock.cancelJob(id);
    },
    getJobAudio(id: string) {
      return current.mode === 'http' ? http.getJobAudio(id) : mock.getJobAudio(id);
    },
    updateSettings(settings: ServerSettings) {
      return current.mode === 'http' ? http.updateSettings(settings) : mock.updateSettings(settings);
    }
  };
}

function mapAdminKey(input: BackendAdminKeyMeta, token?: string): ApiKeyRecord {
  return {
    id: input.key_id,
    name: input.label,
    status: 'active',
    lastUsedAt: input.last_used_at ?? input.created_at,
    createdAt: input.created_at,
    tokenPreview: token ? `qwen_admin_...${token.slice(-4)}` : `${input.key_id.slice(0, 12)}...`,
    rawKey: token,
  };
}
