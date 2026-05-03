import type {
  ApiKeyRecord,
  ComposeRequest,
  DashboardClient,
  DashboardClientConfig,
  JobRecord,
  ModelInfo,
  ServerSettings,
  VoiceProfile
} from '../types';

function isoNow() {
  return new Date().toISOString();
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function uid(prefix: string) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function metricBundle(input: string, taskType: string, stream: boolean, responseFormat: string) {
  const length = Math.max(1, input.length);
  const queueWaitMs = Math.round(120 + length * 0.7);
  const ttfaMs = Math.round(90 + length * 0.25 + (taskType === 'VoiceDesign' ? 35 : 0) - (stream ? 14 : 0));
  const audioDurationMs = Math.round(1000 + length * 2.5);
  const jobWallMs = queueWaitMs + ttfaMs + Math.round(audioDurationMs * (responseFormat === 'pcm' ? 0.12 : 0.18));
  const realtimeX = Number((audioDurationMs / Math.max(jobWallMs - queueWaitMs, 1)).toFixed(2));
  return { queueWaitMs, ttfaMs, audioDurationMs, jobWallMs, realtimeX };
}

function createMockAudioBlob(durationMs = 1200, sampleRate = 24000) {
  const sampleCount = Math.max(1, Math.floor(sampleRate * durationMs / 1000));
  const dataSize = sampleCount * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeText(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeText(8, 'WAVE');
  writeText(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, 'data');
  view.setUint32(40, dataSize, true);

  for (let index = 0; index < sampleCount; index += 1) {
    const sample = Math.sin((2 * Math.PI * 220 * index) / sampleRate);
    view.setInt16(44 + index * 2, Math.round(sample * 0x2fff), true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

function initialModels(): ModelInfo[] {
  return [
    {
      id: 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
      label: '0.6B CustomVoice',
      loaded: true,
      taskTypes: ['CustomVoice'],
      status: 'warm'
    },
    {
      id: 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign',
      label: '1.7B VoiceDesign',
      loaded: false,
      taskTypes: ['VoiceDesign'],
      status: 'cold'
    },
    {
      id: 'Qwen/Qwen3-TTS-12Hz-0.6B-Base',
      label: '0.6B Base',
      loaded: false,
      taskTypes: ['Base'],
      status: 'cold'
    }
  ];
}

function initialVoices(): VoiceProfile[] {
  return [
    {
      id: 'voice_ryan',
      name: 'Ryan',
      kind: 'built-in',
      model: 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
      language: 'German',
      style: 'Calm and focused',
      sampleLabel: 'Built-in voice',
      consent: true
    },
    {
      id: 'voice_mira',
      name: 'Mira',
      kind: 'built-in',
      model: 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
      language: 'English',
      style: 'Clear and friendly',
      sampleLabel: 'Built-in voice',
      consent: true
    }
  ];
}

function initialKeys(): ApiKeyRecord[] {
  return [
    {
      id: 'key_admin',
      name: 'Master Admin Key',
      status: 'active',
      lastUsedAt: isoNow(),
      createdAt: isoNow(),
      tokenPreview: 'qwen_admin_****'
    }
  ];
}

function initialJobs(): JobRecord[] {
  return [];
}

function initialSettings(config: DashboardClientConfig): ServerSettings {
  return {
    mode: config.mode,
    baseUrl: config.baseUrl,
    modelDirectory: 'x:/dev/G3_QWEN_TTS/models',
    defaultModel: 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
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
  };
}

export function createMockDashboardClient(initialConfig: DashboardClientConfig): DashboardClient {
  let config = initialConfig;
  const listeners = new Set<() => void>();
  const state = {
    overview: {
      activeModel: 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
      queueDepth: 1,
      workerState: 'running',
      ttfaMsAvg: 146,
      queueWaitMsAvg: 201,
      jobWallMsAvg: 1115,
      realtimeXAvg: 1.9,
      jobsTotal: 143,
      audioSecondsTotal: 912.4,
      gpuMemoryUsedMb: 3780,
      gpuMemoryTotalMb: 8151,
      gpuUtilizationPct: 42,
      gpuTemperatureC: 49
    },
    models: initialModels(),
    jobs: initialJobs(),
    voices: initialVoices(),
    keys: initialKeys(),
    settings: initialSettings(initialConfig),
    transcriptionHint: 'Upload a clip, run Whisper, then edit the reference text before saving the voice profile.'
  };

  function emit() {
    for (const listener of listeners) {
      listener();
    }
  }

  function recomputeOverview() {
    const metrics = state.jobs.map((job) => job.metrics).filter(Boolean) as NonNullable<JobRecord['metrics']>[];
    const count = Math.max(metrics.length, 1);
    state.overview.queueDepth = state.jobs.filter((job) => job.status === 'queued' || job.status === 'warming').length;
    state.overview.workerState = state.jobs.some((job) => job.status === 'running' || job.status === 'streaming') ? 'running' : 'idle';
    state.overview.ttfaMsAvg = Math.round(metrics.reduce((sum, metric) => sum + metric.ttfaMs, 0) / count);
    state.overview.queueWaitMsAvg = Math.round(metrics.reduce((sum, metric) => sum + metric.queueWaitMs, 0) / count);
    state.overview.jobWallMsAvg = Math.round(metrics.reduce((sum, metric) => sum + metric.jobWallMs, 0) / count);
    state.overview.realtimeXAvg = Number((metrics.reduce((sum, metric) => sum + metric.realtimeX, 0) / count).toFixed(2));
  }

  return {
    mode: 'mock',
    updateConfig(nextConfig: DashboardClientConfig) {
      if (
        config.mode === nextConfig.mode &&
        config.baseUrl === nextConfig.baseUrl &&
        config.adminKey === nextConfig.adminKey
      ) {
        return;
      }
      config = nextConfig;
      state.settings = initialSettings(nextConfig);
      emit();
    },
    async verifyAdminKey(adminKey: string) {
      return {
        id: 'key_admin',
        name: 'Master Admin Key',
        status: 'active' as const,
        lastUsedAt: isoNow(),
        createdAt: isoNow(),
        tokenPreview: `qwen_admin_...${adminKey.slice(-4) || 'mock'}`,
        rawKey: adminKey,
      };
    },
    async rotateAdminKey() {
      const token = `qwen_tts_mock_${Math.random().toString(36).slice(2, 12)}`;
      const record: ApiKeyRecord = {
        id: 'key_admin',
        name: 'Master Admin Key',
        status: 'active' as const,
        lastUsedAt: isoNow(),
        createdAt: isoNow(),
        tokenPreview: `qwen_admin_...${token.slice(-4)}`,
        rawKey: token,
      };
      state.keys = [record];
      emit();
      return clone(record);
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async getSnapshot() {
      return clone({
        ...state
      }) as unknown as Snapshot;
    },
    async createSpeechJob(request: ComposeRequest) {
      const metrics = metricBundle(request.input, request.taskType, request.stream, request.responseFormat);
      const job: JobRecord = {
        id: uid('job'),
        createdAt: isoNow(),
        updatedAt: isoNow(),
        status: request.stream ? 'streaming' : 'queued',
        model: request.model,
        taskType: request.taskType,
        voice: request.voice,
        language: request.language,
        input: request.input,
        instructions: request.instructions,
        responseFormat: request.responseFormat,
        stream: request.stream,
        queuePosition: state.jobs.filter((entry) => entry.status === 'queued').length,
        etaMs: metrics.jobWallMs,
        progress: request.stream ? 42 : 0,
        metrics,
        audioUrl: request.stream ? '/mock/audio/stream.pcm' : '/mock/audio/output.wav'
      };

      state.jobs.unshift(job);
      recomputeOverview();
      emit();
      return clone(job);
    },
    async createVoiceProfile(input: any) {
      const voice: VoiceProfile = {
        id: uid('voice'),
        name: input.name,
        kind: 'custom',
        model: input.model,
        language: input.language,
        style: input.style,
        sampleLabel: input.sampleFileName,
        consent: input.consent
      };
      state.voices.unshift(voice);
      emit();
      return clone(voice);
    },
    async transcribeSample(input: { file: File | null; fileName: string }) {
      const fileName = input.fileName;
      return {
        transcription: `Transcription draft for ${fileName}. Keep this sentence short and practical.`,
        voiceVector: [0.12, -0.33, 0.81, 0.04, 0.66]
      };
    },
    async createApiKey(name: string) {
      const record: ApiKeyRecord = {
        id: uid('key'),
        name,
        status: 'active' as const,
        lastUsedAt: isoNow(),
        createdAt: isoNow(),
        tokenPreview: `${name.toLowerCase().replace(/\s+/g, '_')}_****`
      };
      state.keys.unshift(record);
      emit();
      return clone(record);
    },
    async deleteApiKey(id: string) {
      state.keys = state.keys.filter((record) => record.id !== id);
      emit();
    },
    async cancelJob(id: string) {
      state.jobs = state.jobs.map((record) => (
        record.id === id
          ? {
              ...record,
              status: 'cancelled' as const,
              updatedAt: isoNow(),
              progress: 100,
              errorMessage: 'Cancelled by user.'
            }
          : record
      ));
      recomputeOverview();
      emit();
    },
    async getJobAudio(id: string) {
      const job = state.jobs.find((entry) => entry.id === id);
      return createMockAudioBlob(job?.metrics?.audioDurationMs ?? 1200);
    },
    async updateSettings(settings: ServerSettings) {
      state.settings = { ...settings };
      emit();
      return clone(state.settings);
    }
  };
}
