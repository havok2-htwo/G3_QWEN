import { useEffect, useState, type FormEvent } from 'react';
import { InfoTip } from '../components/InfoTip';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import type { ServerSettings } from '../types';

interface SettingsViewProps {
  value: ServerSettings;
  onChange: (next: ServerSettings) => void;
  onSave: (next: ServerSettings) => Promise<void>;
}

const presetStorageKey = 'qwen-tts-runtime-presets-v1';

type RuntimePresetSettings = Pick<
  ServerSettings,
  | 'modelDirectory'
  | 'defaultModel'
  | 'defaultVoice'
  | 'whisperBaseUrl'
  | 'whisperPath'
  | 'retentionDays'
  | 'queueLimit'
  | 'allowModelDownloads'
  | 'preferredDevice'
  | 'attentionImplementation'
  | 'torchDtype'
  | 'pollIntervalMs'
  | 'theme'
>;

interface RuntimePreset {
  id: string;
  label: string;
  createdAt: string;
  settings: RuntimePresetSettings;
}

function readPresets(): RuntimePreset[] {
  try {
    const raw = localStorage.getItem(presetStorageKey);
    if (!raw) {
      return [];
    }

    return JSON.parse(raw) as RuntimePreset[];
  } catch {
    return [];
  }
}

function toPresetSettings(value: ServerSettings): RuntimePresetSettings {
  return {
    modelDirectory: value.modelDirectory,
    defaultModel: value.defaultModel,
    defaultVoice: value.defaultVoice,
    whisperBaseUrl: value.whisperBaseUrl,
    whisperPath: value.whisperPath,
    retentionDays: value.retentionDays,
    queueLimit: value.queueLimit,
    allowModelDownloads: value.allowModelDownloads,
    preferredDevice: value.preferredDevice,
    attentionImplementation: value.attentionImplementation,
    torchDtype: value.torchDtype,
    pollIntervalMs: value.pollIntervalMs,
    theme: value.theme,
  };
}

export function SettingsView({ value, onChange, onSave }: SettingsViewProps) {
  const [status, setStatus] = useState('');
  const [presets, setPresets] = useState<RuntimePreset[]>(() => readPresets());
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presetName, setPresetName] = useState('');

  useEffect(() => {
    try {
      localStorage.setItem(presetStorageKey, JSON.stringify(presets));
    } catch {
      // ignore storage errors
    }
  }, [presets]);

  function patch<K extends keyof ServerSettings>(key: K, nextValue: ServerSettings[K]) {
    onChange({ ...value, [key]: nextValue });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus('Saving runtime settings...');
    try {
      await onSave(value);
      setStatus('Runtime settings synced.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Runtime settings could not be saved.');
    }
  }

  async function handleApplyPreset() {
    const preset = presets.find((entry) => entry.id === selectedPresetId);
    if (!preset) {
      setStatus('Select a preset first.');
      return;
    }

    const merged = { ...value, ...preset.settings };
    onChange(merged);
    setStatus(`Applying preset "${preset.label}"...`);

    try {
      await onSave(merged);
      setStatus(`Preset "${preset.label}" applied and synced.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Preset could not be applied.');
    }
  }

  function handleSavePreset() {
    const label = presetName.trim();
    if (!label) {
      setStatus('Enter a preset name first.');
      return;
    }

    const nextPreset: RuntimePreset = {
      id: `preset_${Date.now().toString(36)}`,
      label,
      createdAt: new Date().toISOString(),
      settings: toPresetSettings(value),
    };

    setPresets((current) => [nextPreset, ...current.filter((entry) => entry.label !== label)]);
    setSelectedPresetId(nextPreset.id);
    setPresetName('');
    setStatus(`Preset "${label}" saved locally in this browser.`);
  }

  function handleDeletePreset() {
    if (!selectedPresetId) {
      setStatus('Select a preset first.');
      return;
    }

    const preset = presets.find((entry) => entry.id === selectedPresetId);
    setPresets((current) => current.filter((entry) => entry.id !== selectedPresetId));
    setSelectedPresetId('');
    setStatus(preset ? `Preset "${preset.label}" removed.` : 'Preset removed.');
  }

  return (
    <div className="view-grid">
      <SectionHeader
        title="Settings"
        subtitle="Bring the runtime controls closer to the TADA admin layout with presets, tooltips, and clearer operator groupings."
      />

      <div className="two-column">
        <Panel title="Preset Library" subtitle="Store common operator configurations locally and apply them in one step.">
          <div className="form-grid">
            <div className="preset-toolbar">
              <label>
                <span className="field-label">
                  Saved presets
                  <InfoTip text="Presets are stored in the current browser only. They capture the runtime controls shown on this page, not the admin key." />
                </span>
                <select value={selectedPresetId} onChange={(event) => setSelectedPresetId(event.target.value)}>
                  <option value="">Select preset...</option>
                  {presets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.label}
                    </option>
                  ))}
                </select>
              </label>
              <button className="secondary-button" type="button" onClick={() => void handleApplyPreset()}>
                Apply Preset
              </button>
            </div>

            <div className="preset-toolbar">
              <label>
                <span className="field-label">
                  New preset name
                  <InfoTip text="Use presets for different devices, queue sizes, or Whisper routing targets without retyping the same values." />
                </span>
                <input value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="e.g. RTX 5070 low-latency" />
              </label>
              <div className="stack-row-meta">
                <button className="primary-button" type="button" onClick={handleSavePreset}>
                  Save Preset
                </button>
                <button className="ghost-button" type="button" onClick={handleDeletePreset} disabled={!selectedPresetId}>
                  Delete
                </button>
              </div>
            </div>

            <div className="callout subtle-callout">
              <strong>Backend scope</strong>
              <p>
                This QWEN runtime already exposes device, queue, model-download, Whisper, dtype, and theme controls.
                Voice-activity trimming fields such as VAD thresholds are not part of the live backend contract yet.
              </p>
            </div>
          </div>
        </Panel>

        <Panel title="Connection" subtitle="Switch between mock and live admin backends without rebuilding the UI.">
          <form className="form-grid" onSubmit={handleSubmit}>
            <div className="form-row">
              <label>
                <span className="field-label">
                  Mode
                  <InfoTip text="Use mock mode when you want to inspect the UI without a running backend. Use http mode for the real local control room." />
                </span>
                <select value={value.mode} onChange={(event) => patch('mode', event.target.value as ServerSettings['mode'])}>
                  <option value="mock">mock</option>
                  <option value="http">http</option>
                </select>
              </label>
              <label>
                <span className="field-label">
                  Base URL
                  <InfoTip text="Operator API root for the QWEN TTS backend. The admin dashboard uses this endpoint for stats, jobs, settings, and voices." />
                </span>
                <input value={value.baseUrl} onChange={(event) => patch('baseUrl', event.target.value)} />
              </label>
            </div>
            <div className="readonly-grid">
              <div className="access-card">
                <span>Runtime backend</span>
                <strong>{value.runtimeBackend}</strong>
              </div>
              <div className="access-card">
                <span>Sample rate</span>
                <strong>{value.sampleRate} Hz</strong>
              </div>
              <div className="access-card">
                <span>Built-in voices</span>
                <strong>{value.builtInVoices.length}</strong>
              </div>
            </div>
          </form>
        </Panel>
      </div>

      <Panel title="Runtime Controls" subtitle="Group the operator-facing backend settings into the same kind of explicit widgets used in the TADA admin.">
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="settings-grid">
            <label>
              <span className="field-label">
                Default model
                <InfoTip text="Active synthesis model used when operators queue a request without overriding the model manually." />
              </span>
              <input value={value.defaultModel} onChange={(event) => patch('defaultModel', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Default voice
                <InfoTip text="Default built-in voice shown when the form opens in CustomVoice mode." />
              </span>
              <input value={value.defaultVoice} onChange={(event) => patch('defaultVoice', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Preferred device
                <InfoTip text="Primary execution device for the runtime, for example cuda:0 or cpu." />
              </span>
              <input value={value.preferredDevice} onChange={(event) => patch('preferredDevice', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Torch dtype
                <InfoTip text="Floating-point precision used by the runtime. Lower precision can reduce memory usage and improve speed." />
              </span>
              <select value={value.torchDtype} onChange={(event) => patch('torchDtype', event.target.value)}>
                <option value="float32">float32</option>
                <option value="float16">float16</option>
                <option value="bfloat16">bfloat16</option>
              </select>
            </label>
            <label>
              <span className="field-label">
                Attention implementation
                <InfoTip text="Backend attention kernel selection. Keep this aligned with the runtime environment and GPU support." />
              </span>
              <input value={value.attentionImplementation} onChange={(event) => patch('attentionImplementation', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Model directory
                <InfoTip text="Local filesystem path where the runtime expects QWEN model folders." />
              </span>
              <input value={value.modelDirectory} onChange={(event) => patch('modelDirectory', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Whisper base URL
                <InfoTip text="Base URL of the Whisper-compatible helper used for reference-text drafting in the Voice Lab." />
              </span>
              <input value={value.whisperBaseUrl} onChange={(event) => patch('whisperBaseUrl', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Whisper path
                <InfoTip text="Path appended to the Whisper base URL when the voice assistant submits a transcription request." />
              </span>
              <input value={value.whisperPath} onChange={(event) => patch('whisperPath', event.target.value)} />
            </label>
            <label>
              <span className="field-label">
                Queue limit
                <InfoTip text="Maximum number of queued synthesis jobs. New requests are rejected when the queue is full." />
              </span>
              <input type="number" min={1} max={512} value={value.queueLimit} onChange={(event) => patch('queueLimit', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Active requests
                <InfoTip text="How many requests the single scheduler may keep active while it forms compatible sentence batches." />
              </span>
              <input type="number" min={1} max={64} value={value.maxParallelRequests} onChange={(event) => patch('maxParallelRequests', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Batch size
                <InfoTip text="Maximum sentence items per inference batch. Eight is the TTFT-focused default for Qwen." />
              </span>
              <input type="number" min={1} max={64} value={value.maxBatchSize} onChange={(event) => patch('maxBatchSize', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Batch wait ms
                <InfoTip text="Short collection window before the first batch so compatible work can join without adding much TTFT." />
              </span>
              <input type="number" min={0} max={1000} value={value.batchWaitMs} onChange={(event) => patch('batchWaitMs', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Stream chunk ms
                <InfoTip text="Target PCM chunk size for non-native streaming and final sentence flushes." />
              </span>
              <input type="number" min={20} max={1000} value={value.streamChunkMs} onChange={(event) => patch('streamChunkMs', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Stream prebuffer ms
                <InfoTip text="Optional native-stream prebuffer. Keep this at 0 for best TTFT." />
              </span>
              <input type="number" min={0} max={5000} value={value.streamPrebufferMs} onChange={(event) => patch('streamPrebufferMs', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Retention days
                <InfoTip text="How long completed job data and related history stay available to the control room." />
              </span>
              <input type="number" min={1} max={365} value={value.retentionDays} onChange={(event) => patch('retentionDays', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Poll interval (ms)
                <InfoTip text="Refresh cadence for the admin UI. Lower values feel more live but increase request frequency." />
              </span>
              <input type="number" min={250} max={5000} value={value.pollIntervalMs} onChange={(event) => patch('pollIntervalMs', Number(event.target.value))} />
            </label>
            <label>
              <span className="field-label">
                Theme
                <InfoTip text="Visual presentation for the control room. Onyx is the darker TADA/Whisper-aligned option." />
              </span>
              <select value={value.theme} onChange={(event) => patch('theme', event.target.value as ServerSettings['theme'])}>
                <option value="onyx">onyx</option>
                <option value="ember">ember</option>
              </select>
            </label>
          </div>

          <div className="settings-toggle-grid">
            <label className="checkbox-row">
              <input type="checkbox" checked={value.sentenceChunking} onChange={(event) => patch('sentenceChunking', event.target.checked)} />
              <span>
                Sentence chunking
                <InfoTip text="When enabled, the server splits long API input into sentence items for batching and ordered streaming." />
              </span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={value.allowModelDownloads} onChange={(event) => patch('allowModelDownloads', event.target.checked)} />
              <span>
                Allow model downloads
                <InfoTip text="When enabled, the backend may download missing supported models into the configured model directory." />
              </span>
            </label>
          </div>

          <div className="button-row">
            <button className="primary-button" type="submit">
              Save Settings
            </button>
          </div>

          <p className="inline-note">{status || 'The admin key itself is managed separately in the Admin Key tab.'}</p>
        </form>
      </Panel>
    </div>
  );
}
