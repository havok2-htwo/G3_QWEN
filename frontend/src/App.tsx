// @ts-nocheck
import { useEffect, useMemo, useState } from 'react';
import { createDashboardClient } from './api/client';
import { AppShell } from './components/AppShell';
import { BenchmarkView } from './views/BenchmarkView';
import { ComposeView } from './views/ComposeView';
import { JobsView } from './views/JobsView';
import { KeysView } from './views/KeysView';
import { OverviewView } from './views/OverviewView';
import { SettingsView } from './views/SettingsView';
import { VoicesView } from './views/VoicesView';
import type {
  BenchmarkRunDraft,
  ComposeRequest,
  DashboardClient,
  ServerSettings,
  Snapshot,
  TabKey
} from './types';

const settingsStorageKey = 'qwen-tts-dashboard-settings-v2';
const tabStorageKey = 'qwen-tts-dashboard-tab-v2';

const defaultSettings: ServerSettings = {
  mode: 'http',
  baseUrl: 'http://127.0.0.1:8088',
  apiKey: 'mein-geheimer-key-1234',
  modelDirectory: 'x:/dev/G3_QWEN_TTS/models',
  defaultModel: 'Qwen3-TTS-12Hz-0.6B-CustomVoice',
  defaultVoice: 'Ryan',
  whisperBaseUrl: 'http://127.0.0.1:9000',
  whisperPath: '/transcribe/',
  retentionDays: 7,
  queueLimit: 32,
  pollIntervalMs: 1000,
  theme: 'onyx',
  builtInVoices: ['Ryan', 'Vivian', 'Serena', 'Aiden', 'Mia', 'Nova', 'Ava']
};

function loadSettings(): ServerSettings {
  try {
    const raw = localStorage.getItem(settingsStorageKey);
    if (!raw) {
      return defaultSettings;
    }

    return { ...defaultSettings, ...JSON.parse(raw) } as ServerSettings;
  } catch {
    return defaultSettings;
  }
}

function loadTab(): TabKey {
  const raw = localStorage.getItem(tabStorageKey);
  if (
    raw === 'overview' ||
    raw === 'jobs' ||
    raw === 'compose' ||
    raw === 'voices' ||
    raw === 'benchmark' ||
    raw === 'keys' ||
    raw === 'settings'
  ) {
    return raw;
  }

  return 'overview';
}

function usesDefaultConnection(settings: ServerSettings) {
  return (
    settings.mode === defaultSettings.mode &&
    settings.baseUrl === defaultSettings.baseUrl &&
    settings.apiKey === defaultSettings.apiKey
  );
}

export function App() {
  const [settings, setSettings] = useState<ServerSettings>(() => loadSettings());
  const [activeTab, setActiveTab] = useState<TabKey>(() => loadTab());
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [recoveredDefaultConnection, setRecoveredDefaultConnection] = useState(false);

  const client = useMemo<DashboardClient>(
    () =>
      createDashboardClient({
        mode: settings.mode,
        baseUrl: settings.baseUrl,
        apiKey: settings.apiKey
      }),
    []
  );
  const hasSnapshot = snapshot !== null;


  useEffect(() => {
    localStorage.setItem(settingsStorageKey, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    client.updateConfig({
      mode: settings.mode,
      baseUrl: settings.baseUrl,
      apiKey: settings.apiKey
    });
    setRefreshTick((current) => current + 1);
  }, [client, settings.mode, settings.baseUrl, settings.apiKey]);

  useEffect(() => {
    localStorage.setItem(tabStorageKey, activeTab);
  }, [activeTab]);

  useEffect(() => {
    let active = true;

    async function refresh() {
      try {
        const next = await client.getSnapshot();
        if (active) {
          setSnapshot(next);
          setSnapshotError(null);
          setSettings((current) => ({
            ...current,
            ...next.settings,
            mode: current.mode
          }));
        }
      } catch (err: any) {
        if (active) {
          const message = err?.message || 'Failed to connect to backend.';

          if (!hasSnapshot && !recoveredDefaultConnection && !usesDefaultConnection(settings)) {
            setRecoveredDefaultConnection(true);
            setSnapshotError('Retrying the default local backend connection...');
            setSettings((current) => ({
              ...current,
              mode: defaultSettings.mode,
              baseUrl: defaultSettings.baseUrl,
              apiKey: defaultSettings.apiKey
            }));
            return;
          }

          setSnapshotError(message);
        }
      }
    }

    void refresh();

    const unsubscribe = client.subscribe?.(() => {
      void refresh();
    });

    const timer = window.setInterval(() => {
      void refresh();
    }, Math.max(500, settings.pollIntervalMs));

    return () => {
      active = false;
      unsubscribe?.();
      window.clearInterval(timer);
    };
  }, [client, hasSnapshot, recoveredDefaultConnection, refreshTick, settings.mode, settings.baseUrl, settings.apiKey, settings.pollIntervalMs]);

  async function handleComposeSubmit(request: ComposeRequest) {
    await client.createSpeechJob(request);
    setActiveTab('jobs');
  }

  async function handleCreateVoiceProfile(input: Parameters<DashboardClient['createVoiceProfile']>[0]) {
    await client.createVoiceProfile(input);
    setActiveTab('voices');
  }

  async function handleCreateKey(name: string) {
    const key = await client.createApiKey(name);
    return key;
  }

  async function handleDeleteKey(id: string) {
    await client.deleteApiKey(id);
  }

  async function handleBenchmarkRun(draft: BenchmarkRunDraft) {
    await client.createBenchmarkRun(draft);
    setActiveTab('benchmark');
  }

  async function handleSaveSettings(next: ServerSettings) {
    const saved = await client.updateSettings(next);
    setSettings(saved);
  }

  async function handleTranscribeSample(input: { file: File | null; fileName: string }) {
    return client.transcribeSample(input);
  }

  async function handleCancelJob(id: string) {
    await client.cancelJob(id);
  }

  async function handleLoadJobAudio(id: string) {
    return client.getJobAudio(id);
  }

  async function handleModelChange(modelId: string) {
    await client.updateSettings({ ...settings, defaultModel: modelId });
  }

  return (
    <div className={settings.theme === 'ember' ? 'theme-ember app-root' : 'app-root'}>
      <AppShell activeTab={activeTab} onTabChange={setActiveTab} snapshot={snapshot} settings={settings} clientMode={settings.mode} onModelChange={handleModelChange}>
        {activeTab === 'settings' ? (
          <SettingsView value={settings} onChange={setSettings} onSave={handleSaveSettings} />
        ) : snapshot ? (
          <>
            {activeTab === 'overview' && <OverviewView stats={snapshot.overview} jobs={snapshot.jobs} models={snapshot.models} settings={snapshot.settings} />}
            {activeTab === 'jobs' && <JobsView jobs={snapshot.jobs} onCancelJob={handleCancelJob} onLoadJobAudio={handleLoadJobAudio} />}
            {activeTab === 'compose' && (
              <ComposeView
                defaultModel={snapshot.settings.defaultModel}
                defaultVoice={snapshot.settings.defaultVoice}
                models={snapshot.models}
                voices={snapshot.voices}
                builtInVoices={snapshot.settings.builtInVoices ?? []}
                onSubmit={handleComposeSubmit}
              />
            )}
            {activeTab === 'voices' && (
              <VoicesView
                voices={snapshot.voices}
                transcriptionHint={snapshot.transcriptionHint}
                onCreateVoiceProfile={handleCreateVoiceProfile}
                onTranscribeSample={handleTranscribeSample}
              />
            )}
            {activeTab === 'benchmark' && <BenchmarkView runs={snapshot.benchmarkRuns} onRunBenchmark={handleBenchmarkRun} />}
            {activeTab === 'keys' && <KeysView keys={snapshot.keys} onCreateKey={handleCreateKey} onDeleteKey={handleDeleteKey} />}
          </>
        ) : (
          <div className="loading-state">
            <div className="loading-card">
              {snapshotError ? (
                <>
                  <span className="error-icon" style={{ color: 'red', fontSize: '24px' }}>⚠️</span>
                  <strong style={{ marginTop: '8px' }}>Connection Error</strong>
                  <p style={{ color: 'red', marginTop: '4px' }}>{snapshotError}</p>
                </>
              ) : (
                <>
                  <span className="spinner" />
                  <strong>Booting dashboard</strong>
                  <p>Waiting for the first snapshot.</p>
                </>
              )}
            </div>
          </div>
        )}
      </AppShell>
    </div>
  );
}
