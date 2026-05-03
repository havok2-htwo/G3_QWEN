import { useEffect, useMemo, useState } from 'react';
import { createDashboardClient } from './api/client';
import { AppShell } from './components/AppShell';
import { InfoTip } from './components/InfoTip';
import { ComposeView } from './views/ComposeView';
import { JobsView } from './views/JobsView';
import { KeysView } from './views/KeysView';
import { OverviewView } from './views/OverviewView';
import { SettingsView } from './views/SettingsView';
import { VoicesView } from './views/VoicesView';
import type {
  ApiKeyRecord,
  ComposeRequest,
  DashboardClient,
  OverviewHistoryPoint,
  ServerSettings,
  Snapshot,
  TabKey,
} from './types';

type AppRoute = 'landing' | 'admin';

const settingsStorageKey = 'qwen-tts-control-room-settings-v3';
const tabStorageKey = 'qwen-tts-control-room-tab-v3';
const adminKeyStorageKey = 'g3-qwen-tts-admin-key';

const defaultSettings: ServerSettings = {
  mode: 'http',
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
};

function getRouteFromLocation(): AppRoute {
  return window.location.pathname.startsWith('/admin') ? 'admin' : 'landing';
}

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
    raw === 'keys' ||
    raw === 'settings'
  ) {
    return raw;
  }

  return 'overview';
}

function readStoredAdminKey() {
  try {
    return localStorage.getItem(adminKeyStorageKey) || sessionStorage.getItem(adminKeyStorageKey) || '';
  } catch {
    return '';
  }
}

function writeStoredAdminKey(value: string) {
  try {
    localStorage.setItem(adminKeyStorageKey, value);
    sessionStorage.setItem(adminKeyStorageKey, value);
  } catch {
    // ignore storage errors
  }
}

function clearStoredAdminKey() {
  try {
    localStorage.removeItem(adminKeyStorageKey);
    sessionStorage.removeItem(adminKeyStorageKey);
  } catch {
    // ignore storage errors
  }
}

function getErrorMessage(error: unknown) {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return 'The control room could not reach the backend.';
}

function isUnauthorizedError(error: unknown) {
  return typeof error === 'object' && error !== null && 'status' in error && Number((error as { status?: number }).status) === 401;
}

function navigateTo(route: AppRoute) {
  const nextPath = route === 'admin' ? '/admin' : '/';
  if (window.location.pathname !== nextPath) {
    window.history.pushState({}, '', nextPath);
  }
}

function LandingPage({ onOpenAdmin }: { onOpenAdmin: () => void }) {
  return (
    <main className="landing-page">
      <section className="landing-hero">
        <div className="landing-copy">
          <p className="eyebrow">Powered by SONS</p>
          <h1>G3 QWEN TTS</h1>
          <p className="landing-lead">
            Company-grade low-latency synthesis for private QWEN deployments, voice assets, queue control,
            and runtime oversight.
          </p>
          <div className="landing-actions">
            <button className="primary-button" type="button" onClick={onOpenAdmin}>
              Open Control Room
            </button>
            <a className="secondary-link" href="/docs">
              Open API Docs
            </a>
          </div>
        </div>

        <div className="landing-card-grid">
          <article className="landing-card">
            <span className="field-label">
              Admin entry
              <InfoTip text="The private control room is protected separately from the public landing page and expects the admin key header on authenticated requests." />
            </span>
            <strong>Protected by X-Admin-Key</strong>
            <p>The browser stores the key locally and sends it only to the private control room.</p>
          </article>
          <article className="landing-card">
            <span className="field-label">
              Operations
              <InfoTip text="The operator surface combines runtime monitoring, history review, model switching, and voice management in one place." />
            </span>
            <strong>History, models, voices</strong>
            <p>Watch worker state, switch active models, and manage custom voice profiles from one surface.</p>
          </article>
          <article className="landing-card">
            <span className="field-label">
              Synthesis
              <InfoTip text="Compose requests, benchmark different models, and inspect finished audio without leaving the admin workflow." />
            </span>
            <strong>Low-latency workflow</strong>
            <p>Compose jobs, benchmark throughput, and inspect generated audio without leaving the admin panel.</p>
          </article>
        </div>
      </section>

      <section className="landing-links">
        <a className="link-card" href="/admin">
          <strong>Control Room</strong>
          <span>Private admin workspace for runtime, voices, queue depth, and access rotation.</span>
        </a>
        <a className="link-card" href="/docs">
          <strong>FastAPI Docs</strong>
          <span>Inspect the live backend schema and test protected endpoints locally when needed.</span>
        </a>
        <a className="link-card" href="https://qwenlm.github.io/" target="_blank" rel="noreferrer">
          <strong>QWEN Context</strong>
          <span>Reference model family details separately from the local Genesis control surface.</span>
        </a>
      </section>
    </main>
  );
}

function AdminGate({
  adminKeyInput,
  error,
  isAuthenticating,
  onChange,
  onOpen,
}: {
  adminKeyInput: string;
  error: string | null;
  isAuthenticating: boolean;
  onChange: (value: string) => void;
  onOpen: () => void;
}) {
  return (
    <main className="auth-shell">
      <section className="panel auth-panel">
        <p className="eyebrow">Private Access</p>
        <h1>QWEN TTS Control Room</h1>
        <p className="auth-copy">
          The synth engine stays local. This control room is protected by the master admin key and is intended for
          operators, not end users.
        </p>
        <label className="form-grid">
          <span className="field-label">
            Admin Key
            <InfoTip text="Paste the current master admin key. The browser stores it locally after verification and sends it only to protected control-room endpoints." />
          </span>
          <input
            value={adminKeyInput}
            onChange={(event) => onChange(event.target.value)}
            placeholder="qwen_tts_..."
          />
        </label>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={onOpen} disabled={!adminKeyInput.trim() || isAuthenticating}>
            {isAuthenticating ? 'Checking Key...' : 'Open Control Room'}
          </button>
        </div>
        {error ? <div className="message error">{error}</div> : null}
      </section>
    </main>
  );
}

export function ControlRoomApp() {
  const [route, setRoute] = useState<AppRoute>(() => getRouteFromLocation());
  const [settings, setSettings] = useState<ServerSettings>(() => loadSettings());
  const [activeTab, setActiveTab] = useState<TabKey>(() => loadTab());
  const [adminKey, setAdminKey] = useState(() => readStoredAdminKey());
  const [adminKeyInput, setAdminKeyInput] = useState(() => readStoredAdminKey());
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [overviewHistory, setOverviewHistory] = useState<OverviewHistoryPoint[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [rotatedAdminKey, setRotatedAdminKey] = useState<ApiKeyRecord | null>(null);

  const client = useMemo<DashboardClient>(
    () =>
      createDashboardClient({
        mode: settings.mode,
        baseUrl: settings.baseUrl,
        adminKey,
      }),
    [],
  );

  const adminRecord = snapshot?.keys.find((entry) => entry.name.toLowerCase().includes('admin')) ?? rotatedAdminKey ?? null;

  useEffect(() => {
    const handlePopState = () => setRoute(getRouteFromLocation());
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    localStorage.setItem(settingsStorageKey, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    localStorage.setItem(tabStorageKey, activeTab);
  }, [activeTab]);

  useEffect(() => {
    client.updateConfig({
      mode: settings.mode,
      baseUrl: settings.baseUrl,
      adminKey,
    });
  }, [adminKey, client, settings.baseUrl, settings.mode]);

  useEffect(() => {
    if (route !== 'admin' || !adminKey) {
      return undefined;
    }

    let active = true;

    async function refresh() {
      try {
        const next = await client.getSnapshot();
        if (!active) {
          return;
        }
        setSnapshot(next);
        setRotatedAdminKey(null);
        setSnapshotError(null);
        setSettings((current) => ({
          ...current,
          ...next.settings,
          mode: current.mode,
          baseUrl: current.baseUrl,
        }));
      } catch (refreshError) {
        if (!active) {
          return;
        }
        if (isUnauthorizedError(refreshError)) {
          clearStoredAdminKey();
          setAdminKey('');
          setAdminKeyInput('');
          setSnapshot(null);
          setRotatedAdminKey(null);
          setError('The stored admin key is no longer valid. Please sign in again.');
          return;
        }
        const nextError = getErrorMessage(refreshError);
        setSnapshotError(nextError);
        setError(nextError);
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
  }, [adminKey, client, refreshTick, route, settings.pollIntervalMs]);

  useEffect(() => {
    if (!snapshot) {
      return;
    }

    const nextPoint: OverviewHistoryPoint = {
      recordedAt: new Date().toISOString(),
      queueDepth: snapshot.overview.queueDepth,
      realtimeXAvg: snapshot.overview.realtimeXAvg,
      gpuUtilizationPct: snapshot.overview.gpuUtilizationPct,
      gpuMemoryUsedMb: snapshot.overview.gpuMemoryUsedMb,
    };

    setOverviewHistory((current) => [...current.slice(-119), nextPoint]);
  }, [snapshot]);

  useEffect(() => {
    if (!adminKey || route !== 'admin') {
      setOverviewHistory([]);
    }
  }, [adminKey, route]);

  function persistAdminKey(nextKey: string) {
    writeStoredAdminKey(nextKey);
    setAdminKey(nextKey);
    setAdminKeyInput(nextKey);
  }

  function clearPersistedAdminKey(nextError: string | null = null) {
    clearStoredAdminKey();
    setAdminKey('');
    setSnapshot(null);
    setRotatedAdminKey(null);
    setSnapshotError(null);
    setMessage(null);
    setError(nextError);
  }

  async function handleOpenAdmin() {
    const candidate = adminKeyInput.trim();
    if (!candidate) {
      return;
    }

    setIsAuthenticating(true);
    setMessage(null);
    setError(null);

    try {
      await client.verifyAdminKey(candidate);
      persistAdminKey(candidate);
      navigateTo('admin');
      setRoute('admin');
      setRefreshTick((current) => current + 1);
    } catch (authError) {
      clearStoredAdminKey();
      setAdminKey('');
      setError(getErrorMessage(authError));
    } finally {
      setIsAuthenticating(false);
    }
  }

  async function handleRefresh() {
    setMessage('Refreshing the control room snapshot...');
    setError(null);
    setRefreshTick((current) => current + 1);
  }

  async function handleRotateAdminKey() {
    setMessage(null);
    setError(null);
    try {
      const nextKey = await client.rotateAdminKey();
      if (nextKey.rawKey) {
        persistAdminKey(nextKey.rawKey);
      }
      setRotatedAdminKey(nextKey);
      setMessage('Admin key rotated. The browser is already switched to the new key.');
      setActiveTab('keys');
      setRefreshTick((current) => current + 1);
      return nextKey;
    } catch (rotationError) {
      if (isUnauthorizedError(rotationError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
        return undefined;
      }
      setError(getErrorMessage(rotationError));
      return undefined;
    }
  }

  async function handleComposeSubmit(request: ComposeRequest) {
    try {
      await client.createSpeechJob(request);
      setMessage('Synthesis request queued. The live record is now visible in History.');
      setActiveTab('jobs');
      setRefreshTick((current) => current + 1);
    } catch (submitError) {
      if (isUnauthorizedError(submitError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
      } else {
        setError(getErrorMessage(submitError));
      }
      throw submitError;
    }
  }

  async function handleCreateVoiceProfile(input: Parameters<DashboardClient['createVoiceProfile']>[0]) {
    try {
      await client.createVoiceProfile(input);
      setMessage('Voice profile saved to the library.');
      setActiveTab('voices');
      setRefreshTick((current) => current + 1);
    } catch (voiceError) {
      if (isUnauthorizedError(voiceError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
      } else {
        setError(getErrorMessage(voiceError));
      }
      throw voiceError;
    }
  }

  async function handleSaveSettings(next: ServerSettings) {
    try {
      const saved = await client.updateSettings(next);
      setSettings(saved);
      setMessage('Runtime settings synced with the backend.');
      setRefreshTick((current) => current + 1);
    } catch (settingsError) {
      if (isUnauthorizedError(settingsError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
      } else {
        setError(getErrorMessage(settingsError));
      }
      throw settingsError;
    }
  }

  async function handleTranscribeSample(input: { file: File | null; fileName: string }) {
    try {
      return await client.transcribeSample(input);
    } catch (transcriptionError) {
      if (isUnauthorizedError(transcriptionError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
      } else {
        setError(getErrorMessage(transcriptionError));
      }
      throw transcriptionError;
    }
  }

  async function handleCancelJob(id: string) {
    try {
      await client.cancelJob(id);
      setMessage('Job cancellation requested.');
      setRefreshTick((current) => current + 1);
    } catch (cancelError) {
      if (isUnauthorizedError(cancelError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
        return;
      }
      setError(getErrorMessage(cancelError));
    }
  }

  async function handleLoadJobAudio(id: string) {
    try {
      return await client.getJobAudio(id);
    } catch (audioError) {
      if (isUnauthorizedError(audioError)) {
        clearPersistedAdminKey('Your admin key is no longer valid. Please sign in again.');
      } else {
        setError(getErrorMessage(audioError));
      }
      throw audioError;
    }
  }

  async function handleModelChange(modelId: string) {
    await handleSaveSettings({ ...settings, defaultModel: modelId });
  }

  function openAdminRoute() {
    navigateTo('admin');
    setRoute('admin');
  }

  function openLandingRoute() {
    navigateTo('landing');
    setRoute('landing');
  }

  if (route === 'landing') {
    return (
      <div className={settings.theme === 'ember' ? 'theme-ember app-root' : 'app-root'}>
        <LandingPage onOpenAdmin={openAdminRoute} />
      </div>
    );
  }

  if (!adminKey) {
    return (
      <div className={settings.theme === 'ember' ? 'theme-ember app-root' : 'app-root'}>
        <AdminGate
          adminKeyInput={adminKeyInput}
          error={error}
          isAuthenticating={isAuthenticating}
          onChange={setAdminKeyInput}
          onOpen={handleOpenAdmin}
        />
      </div>
    );
  }

  return (
    <div className={settings.theme === 'ember' ? 'theme-ember app-root' : 'app-root'}>
      <AppShell
        activeTab={activeTab}
        onTabChange={setActiveTab}
        snapshot={snapshot}
        settings={settings}
        clientMode={settings.mode}
        onModelChange={handleModelChange}
        onRefresh={handleRefresh}
        onRotateAdminKey={handleRotateAdminKey}
        onLogout={() => clearPersistedAdminKey(null)}
        onOpenLanding={openLandingRoute}
        adminRecord={adminRecord}
      >
        {message ? <div className="message success">{message}</div> : null}
        {error ? <div className="message error">{error}</div> : null}
        {activeTab === 'settings' ? (
          <SettingsView value={settings} onChange={setSettings} onSave={handleSaveSettings} />
        ) : snapshot ? (
          <>
            {activeTab === 'overview' && (
              <OverviewView
                stats={snapshot.overview}
                history={overviewHistory}
                jobs={snapshot.jobs}
                models={snapshot.models}
                settings={snapshot.settings}
              />
            )}
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
            {activeTab === 'keys' && (
              <KeysView
                adminRecord={adminRecord}
                currentAdminKey={adminKey}
                onRotateAdminKey={handleRotateAdminKey}
                onLogout={() => clearPersistedAdminKey(null)}
              />
            )}
          </>
        ) : (
          <div className="loading-state">
            <div className="loading-card">
              {snapshotError ? (
                <>
                  <strong>Backend connection unavailable</strong>
                  <p>{snapshotError}</p>
                </>
              ) : (
                <>
                  <span className="spinner" />
                  <strong>Booting control room</strong>
                  <p>Waiting for the first backend snapshot.</p>
                </>
              )}
            </div>
          </div>
        )}
      </AppShell>
    </div>
  );
}
