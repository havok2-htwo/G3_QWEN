import { useEffect, useMemo, useRef, useState } from "react";

import {
  apiFetch,
  clearStoredAdminKey,
  createWavBlobFromInt16Chunks,
  decodePcm16Base64,
  formatDate,
  formatMs,
  formatRealtime,
  formatSeconds,
  readStoredAdminKey,
  streamNdjson,
  streamSse,
  writeStoredAdminKey,
  type DashboardSnapshot,
  type JobMetrics,
  type ServerSettings,
  type SynthStreamEvent,
  type TaskType,
} from "../shared/api";

function inferTaskType(modelId: string): TaskType {
  if (modelId.endsWith("VoiceDesign")) return "VoiceDesign";
  if (modelId.endsWith("Base")) return "Base";
  return "CustomVoice";
}

function preferredBaseModel(models: DashboardSnapshot["models"]) {
  return (
    models.find((model) => model.model_id.includes("1.7B-Base"))?.model_id ||
    models.find((model) => model.model_id.includes("0.6B-Base"))?.model_id ||
    ""
  );
}

async function copyToClipboard(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
  }
}

export function AdminApp() {
  const [adminKeyInput, setAdminKeyInput] = useState(() => readStoredAdminKey());
  const [adminKey, setAdminKey] = useState(() => readStoredAdminKey());
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<ServerSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [quickModel, setQuickModel] = useState("");
  const [quickVoice, setQuickVoice] = useState("");
  const [quickText, setQuickText] = useState("Das neue Adminpanel nutzt dieselbe Streaming-Pipeline wie die offene Demo.");
  const [quickInstructions, setQuickInstructions] = useState("");
  const [quickSeed, setQuickSeed] = useState("");
  const [quickMetrics, setQuickMetrics] = useState<JobMetrics | null>(null);
  const [quickAudioUrl, setQuickAudioUrl] = useState("");
  const [quickRunning, setQuickRunning] = useState(false);
  const [voiceName, setVoiceName] = useState("");
  const [voiceRefText, setVoiceRefText] = useState("");
  const [voiceConsent, setVoiceConsent] = useState(false);
  const [voiceFile, setVoiceFile] = useState<File | null>(null);
  const [jobAudioUrls, setJobAudioUrls] = useState<Record<string, string>>({});
  const audioContextRef = useRef<AudioContext | null>(null);
  const nextPlaybackTimeRef = useRef(0);
  const quickAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      Object.values(jobAudioUrls).forEach((url) => URL.revokeObjectURL(url));
      if (quickAudioUrl) URL.revokeObjectURL(quickAudioUrl);
      if (audioContextRef.current) void audioContextRef.current.close().catch(() => undefined);
    };
  }, [jobAudioUrls, quickAudioUrl]);

  async function loadSnapshot(key: string) {
    const data = await apiFetch<DashboardSnapshot>("/api/admin/snapshot", { adminKey: key });
    setSnapshot(data);
    setSettingsDraft(data.settings);
    setQuickModel((current) => current || data.settings.default_model);
  }

  useEffect(() => {
    if (!adminKey) return;
    const controller = new AbortController();
    void streamSse("/api/admin/dashboard/stream", {
      adminKey,
      signal: controller.signal,
      onEvent: async (eventName, payload) => {
        if (eventName === "dashboard.snapshot") {
          const next = payload as DashboardSnapshot;
          setSnapshot(next);
          setSettingsDraft((current) => current ?? next.settings);
        }
      },
    }).catch((streamError) => {
      if (controller.signal.aborted) return;
      if ((streamError as { status?: number }).status === 401) {
        clearStoredAdminKey();
        setAdminKey("");
        setAdminKeyInput("");
        setSnapshot(null);
      } else {
        setError(streamError instanceof Error ? streamError.message : "Dashboard-Stream getrennt.");
      }
    });
    return () => controller.abort();
  }, [adminKey]);

  const models = snapshot?.models ?? [];
  const voices = snapshot?.voices ?? [];
  const quickTaskType = useMemo(() => inferTaskType(quickModel || snapshot?.settings.default_model || ""), [quickModel, snapshot]);
  const quickVoices = useMemo(
    () => {
      if (quickTaskType === "Base") return voices.filter((voice) => voice.source === "custom");
      if (quickTaskType === "CustomVoice") return voices.filter((voice) => voice.source !== "custom");
      return voices;
    },
    [quickTaskType, voices],
  );
  const customVoices = useMemo(() => voices.filter((voice) => voice.source === "custom"), [voices]);

  useEffect(() => {
    if (!quickVoices.length) {
      setQuickVoice("");
      return;
    }
    setQuickVoice((current) =>
      current && quickVoices.some((voice) => voice.voice_id === current || voice.name === current)
        ? current
        : quickVoices[0].voice_id,
    );
  }, [quickVoices]);

  function handleQuickVoiceChange(voiceId: string) {
    const voice = voices.find((item) => item.voice_id === voiceId || item.name === voiceId);
    if (voice?.source === "custom" && quickTaskType !== "Base") {
      const baseModel = preferredBaseModel(models);
      if (baseModel) setQuickModel(baseModel);
    }
    setQuickVoice(voiceId);
  }

  async function handleAuthenticate() {
    if (!adminKeyInput.trim()) return;
    setAuthLoading(true);
    setError("");
    try {
      await loadSnapshot(adminKeyInput.trim());
      writeStoredAdminKey(adminKeyInput.trim());
      setAdminKey(adminKeyInput.trim());
      setMessage("Adminpanel verbunden.");
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : "Authentifizierung fehlgeschlagen.");
    } finally {
      setAuthLoading(false);
    }
  }

  async function ensureAudioContext(nextSampleRate: number) {
    const AudioContextImpl = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextImpl) throw new Error("Web Audio ist nicht verfuegbar.");
    if (audioContextRef.current && audioContextRef.current.sampleRate !== nextSampleRate) {
      await audioContextRef.current.close().catch(() => undefined);
      audioContextRef.current = null;
      nextPlaybackTimeRef.current = 0;
    }
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContextImpl({ sampleRate: nextSampleRate });
      nextPlaybackTimeRef.current = 0;
    }
    if (audioContextRef.current.state === "suspended") await audioContextRef.current.resume();
    return audioContextRef.current;
  }

  async function queuePlayback(float32: Float32Array, nextSampleRate: number) {
    const context = await ensureAudioContext(nextSampleRate);
    const buffer = context.createBuffer(1, float32.length, nextSampleRate);
    buffer.getChannelData(0).set(float32);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.06, nextPlaybackTimeRef.current);
    source.start(startAt);
    nextPlaybackTimeRef.current = startAt + buffer.duration;
  }

  async function handleQuickRun() {
    if (quickRunning) {
      quickAbortRef.current?.abort();
      return;
    }
    if (!quickModel || (quickTaskType !== "VoiceDesign" && !quickVoice)) return;
    if (quickAudioUrl) URL.revokeObjectURL(quickAudioUrl);
    setQuickAudioUrl("");
    setQuickMetrics(null);
    setQuickRunning(true);
    setError("");
    nextPlaybackTimeRef.current = 0;
    const controller = new AbortController();
    let sampleRate = snapshot?.settings.sample_rate ?? 24000;
    const chunks: Int16Array[] = [];
    quickAbortRef.current = controller;
    const parsedSeed = quickSeed.trim() ? Number(quickSeed) : null;

    try {
      await streamNdjson("/api/v1/synthesize/stream", {
        signal: controller.signal,
        body: {
          input: quickText,
          model: quickModel,
          voice: quickTaskType === "VoiceDesign" ? null : quickVoice,
          task_type: quickTaskType,
          instructions: quickInstructions,
          language: "Auto",
          stream: true,
          response_format: "pcm",
          seed: parsedSeed !== null && Number.isFinite(parsedSeed) ? parsedSeed : null,
        },
        onEvent: async (event: SynthStreamEvent) => {
          if (event.type === "chunk") {
            sampleRate = event.sample_rate;
            const decoded = decodePcm16Base64(event.pcm16_b64);
            chunks.push(decoded.int16);
            await queuePlayback(decoded.float32, event.sample_rate);
          }
          if (event.type === "done") setQuickMetrics(event.result.metrics);
          if (event.type === "error") throw new Error(event.message);
        },
      });
      if (chunks.length) setQuickAudioUrl(URL.createObjectURL(createWavBlobFromInt16Chunks(chunks, sampleRate)));
      setMessage("Quick-Synthesis fertig.");
    } catch (quickError) {
      if (!controller.signal.aborted) setError(quickError instanceof Error ? quickError.message : "Quick-Synthesis fehlgeschlagen.");
    } finally {
      if (quickAbortRef.current === controller) quickAbortRef.current = null;
      setQuickRunning(false);
    }
  }

  async function handleRotateKey() {
    if (!adminKey) return;
    try {
      const payload = await apiFetch<{ token: string }>("/api/admin/keys", { method: "POST", adminKey });
      writeStoredAdminKey(payload.token);
      setAdminKey(payload.token);
      setAdminKeyInput(payload.token);
      await copyToClipboard(payload.token);
      setMessage("Admin-Key rotiert und kopiert.");
    } catch (rotateError) {
      setError(rotateError instanceof Error ? rotateError.message : "Key-Rotation fehlgeschlagen.");
    }
  }

  async function handleSaveSettings() {
    if (!adminKey || !settingsDraft) return;
    try {
      const updated = await apiFetch<ServerSettings>("/api/admin/settings", { method: "PUT", adminKey, body: settingsDraft });
      setSettingsDraft(updated);
      await loadSnapshot(adminKey);
      setMessage("Settings gespeichert.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Settings konnten nicht gespeichert werden.");
    }
  }

  async function handleUploadVoice() {
    if (!adminKey || !voiceFile || !voiceName.trim()) return;
    const form = new FormData();
    form.append("audio_sample", voiceFile);
    form.append("name", voiceName.trim());
    form.append("consent", String(voiceConsent));
    form.append("ref_text", voiceRefText);
    try {
      await apiFetch("/api/admin/voices", { method: "POST", adminKey, body: form });
      setVoiceName("");
      setVoiceRefText("");
      setVoiceConsent(false);
      setVoiceFile(null);
      await loadSnapshot(adminKey);
      setMessage("Voice gespeichert.");
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Voice-Upload fehlgeschlagen.");
    }
  }

  async function handleDeleteVoice(voiceId: string) {
    if (!adminKey) return;
    await apiFetch(`/api/admin/voices/${voiceId}`, { method: "DELETE", adminKey });
    await loadSnapshot(adminKey);
  }

  async function handleLoadJobAudio(jobId: string) {
    if (!adminKey) return;
    const blob = await apiFetch<Blob>(`/api/admin/jobs/${jobId}/audio`, { adminKey, responseType: "blob" });
    setJobAudioUrls((current) => ({ ...current, [jobId]: URL.createObjectURL(blob) }));
  }

  async function handleDeleteJob(jobId: string) {
    if (!adminKey) return;
    await apiFetch(`/api/admin/jobs/${jobId}`, { method: "DELETE", adminKey });
    await loadSnapshot(adminKey);
  }

  function handleLogout() {
    clearStoredAdminKey();
    setAdminKey("");
    setAdminKeyInput("");
    setSnapshot(null);
  }

  if (!adminKey || !snapshot || !settingsDraft) {
    return (
      <main className="gate-shell">
        <section className="gate-card">
          <p className="eyebrow">Private Access</p>
          <h1>QWEN Adminpanel</h1>
          <p className="widget-copy">Nur das Adminpanel ist geschuetzt. Die Demo bleibt offen.</p>
          <label>
            Admin-Key
            <input value={adminKeyInput} onChange={(event) => setAdminKeyInput(event.target.value)} placeholder="qwen_tts_..." />
          </label>
          <div className="button-row">
            <button className="primary-button" type="button" onClick={handleAuthenticate} disabled={authLoading}>
              {authLoading ? "Pruefe..." : "Adminpanel oeffnen"}
            </button>
            <a className="ghost-button" href="/demo">Zur Demo</a>
          </div>
          {error ? <div className="message error">{error}</div> : null}
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell admin-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <p className="eyebrow">Admin Panel</p>
          <h1>QWEN Runtime Control</h1>
          <p>Ein Panel fuer Queue, Stimmen, Einstellungen, Models und History. Mehr braucht die App nicht.</p>
        </div>
        <div className="status-grid">
          <div className="status-pill"><span>Model</span><strong>{snapshot.overview.active_model || "-"}</strong></div>
          <div className="status-pill"><span>Queue</span><strong>{snapshot.overview.queue_depth}</strong></div>
          <div className="status-pill"><span>Active requests</span><strong>{snapshot.overview.active_requests}</strong></div>
          <div className="status-pill"><span>Realtime avg</span><strong>{formatRealtime(snapshot.overview.realtime_x_avg)}</strong></div>
        </div>
        <div className="button-row">
          <a className="ghost-button" href="/">Landing</a>
          <a className="secondary-button" href="/demo">Demo</a>
          <button className="secondary-button" type="button" onClick={handleRotateKey}>Rotate API Key</button>
          <button className="ghost-button" type="button" onClick={handleLogout}>Logout</button>
        </div>
      </section>

      {message ? <div className="message success">{message}</div> : null}
      {error ? <div className="message error">{error}</div> : null}

      <section className="widget-grid">
        <section className="widget span-4"><div className="widget-header"><h2>Live Queue</h2></div>
          <div className="metric-list">
            <div className="metric-row"><span>Worker</span><strong>{snapshot.overview.worker_state}</strong></div>
            <div className="metric-row"><span>TTFA avg</span><strong>{formatMs(snapshot.overview.ttfa_ms_avg)}</strong></div>
            <div className="metric-row"><span>Queue wait avg</span><strong>{formatMs(snapshot.overview.queue_wait_ms_avg)}</strong></div>
            <div className="metric-row"><span>Audio total</span><strong>{formatSeconds(snapshot.overview.audio_seconds_total)}</strong></div>
            <div className="metric-row"><span>GPU</span><strong>{snapshot.overview.gpu_utilization_pct}%</strong></div>
          </div>
          {snapshot.current_batch ? <div className="voice-card"><strong>{snapshot.current_batch.batch_id}</strong><div className="inline-pills"><span className="pill">{snapshot.current_batch.model_id}</span><span className="pill">{snapshot.current_batch.task_type}</span><span className="pill">size {snapshot.current_batch.size}</span></div></div> : null}
        </section>

        <section className="widget span-4"><div className="widget-header"><h2>Admin Key</h2></div>
          <div className="metric-list">
            <div className="metric-row"><span>Created</span><strong>{formatDate(snapshot.admin_key.created_at)}</strong></div>
            <div className="metric-row"><span>Last used</span><strong>{formatDate(snapshot.admin_key.last_used_at)}</strong></div>
            <div className="metric-row"><span>Current</span><strong>{adminKey.slice(0, 14)}...</strong></div>
          </div>
          <div className="button-row"><button className="secondary-button" type="button" onClick={() => void copyToClipboard(adminKey)}>Copy</button></div>
        </section>

        <section className="widget span-4"><div className="widget-header"><h2>Models</h2></div>
          <div className="model-list">{models.map((model) => <article key={model.model_id} className="model-card"><strong>{model.model_id}</strong><div className="inline-pills"><span className={`pill ${model.active ? "active" : ""}`}>{model.active ? "active" : "idle"}</span><span className={`pill ${model.loaded ? "active" : ""}`}>{model.loaded ? "loaded" : "cold"}</span></div></article>)}</div>
        </section>

        <section className="widget span-7 audio-card"><div className="widget-header"><h2>Quick Synthesis</h2></div>
          <div className="field-grid two">
            <label>Modell<select value={quickModel} onChange={(event) => setQuickModel(event.target.value)}>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}</select></label>
            <label>Stimme<select value={quickVoice} onChange={(event) => handleQuickVoiceChange(event.target.value)} disabled={quickTaskType === "VoiceDesign"}>{quickVoices.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.name} ({voice.source})</option>)}</select></label>
          </div>
          {customVoices.length > 0 && quickTaskType !== "Base" ? (
            <div className="button-row">
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  const baseModel = preferredBaseModel(models);
                  if (baseModel) {
                    setQuickModel(baseModel);
                    setQuickVoice(customVoices[0].voice_id);
                  }
                }}
              >
                Custom Voice mit Base nutzen
              </button>
            </div>
          ) : null}
          <label>Text<textarea value={quickText} onChange={(event) => setQuickText(event.target.value)} /></label>
          <label>Instructions<textarea value={quickInstructions} onChange={(event) => setQuickInstructions(event.target.value)} /></label>
          <label>Seed<input type="number" min="0" max="2147483647" value={quickSeed} onChange={(event) => setQuickSeed(event.target.value)} placeholder="leer = zufaellig" /></label>
          <div className="button-row"><button className="primary-button" type="button" onClick={handleQuickRun}>{quickRunning ? "Stoppen" : "Stream starten"}</button></div>
          {quickAudioUrl ? <audio controls src={quickAudioUrl} /> : null}
          {quickMetrics ? <div className="metric-list"><div className="metric-row"><span>TTFA</span><strong>{formatMs(quickMetrics.ttfa_ms)}</strong></div><div className="metric-row"><span>Realtime</span><strong>{formatRealtime(quickMetrics.realtime_x)}</strong></div><div className="metric-row"><span>Duration</span><strong>{formatSeconds((quickMetrics.audio_duration_ms || 0) / 1000)}</strong></div></div> : null}
        </section>

        <section className="widget span-5"><div className="widget-header"><h2>Runtime Settings</h2></div>
          <div className="field-grid two">
            <label>Default model<select value={settingsDraft.default_model} onChange={(event) => setSettingsDraft({ ...settingsDraft, default_model: event.target.value })}>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}</select></label>
            <label>Default voice<input value={settingsDraft.default_voice} onChange={(event) => setSettingsDraft({ ...settingsDraft, default_voice: event.target.value })} /></label>
            <label>Queue limit<input type="number" value={settingsDraft.queue_limit} onChange={(event) => setSettingsDraft({ ...settingsDraft, queue_limit: Number(event.target.value) || 1 })} /></label>
            <label>Active requests<input type="number" value={settingsDraft.max_parallel_requests} onChange={(event) => setSettingsDraft({ ...settingsDraft, max_parallel_requests: Number(event.target.value) || 1 })} /></label>
            <label>Batch size<input type="number" value={settingsDraft.max_batch_size} onChange={(event) => setSettingsDraft({ ...settingsDraft, max_batch_size: Number(event.target.value) || 1 })} /></label>
            <label>Batch wait ms<input type="number" value={settingsDraft.batch_wait_ms} onChange={(event) => setSettingsDraft({ ...settingsDraft, batch_wait_ms: Number(event.target.value) || 0 })} /></label>
            <label>Stream prebuffer ms<input type="number" value={settingsDraft.stream_prebuffer_ms} onChange={(event) => setSettingsDraft({ ...settingsDraft, stream_prebuffer_ms: Number(event.target.value) || 0 })} /></label>
          </div>
          <label className="checkbox-row"><input type="checkbox" checked={settingsDraft.sentence_chunking} onChange={(event) => setSettingsDraft({ ...settingsDraft, sentence_chunking: event.target.checked })} />Sentence chunking aktiv</label>
          <div className="button-row"><button className="primary-button" type="button" onClick={handleSaveSettings}>Settings speichern</button></div>
        </section>

        <section className="widget span-6"><div className="widget-header"><h2>Voice Library</h2></div>
          <div className="field-grid"><label>Name<input value={voiceName} onChange={(event) => setVoiceName(event.target.value)} /></label><label>Referenztext<textarea value={voiceRefText} onChange={(event) => setVoiceRefText(event.target.value)} /></label><label>Sample<input type="file" accept="audio/*" onChange={(event) => setVoiceFile(event.target.files?.[0] ?? null)} /></label><label className="checkbox-row"><input type="checkbox" checked={voiceConsent} onChange={(event) => setVoiceConsent(event.target.checked)} />Zustimmung bestaetigt</label><div className="button-row"><button className="primary-button" type="button" onClick={handleUploadVoice}>Voice speichern</button></div></div>
          <div className="voice-list">{voices.map((voice) => <article key={voice.voice_id} className="voice-card"><strong>{voice.name}</strong><div className="inline-pills"><span className="pill">{voice.source}</span><span className="pill">{formatDate(voice.created_at)}</span></div>{voice.source === "custom" ? <div className="button-row"><button className="ghost-button" type="button" onClick={() => void handleDeleteVoice(voice.voice_id)}>Delete</button></div> : null}</article>)}</div>
        </section>

        <section className="widget span-6"><div className="widget-header"><h2>History</h2></div>
          <div className="job-list">{snapshot.jobs.map((job) => <article key={job.job_id} className="job-card"><strong>{job.input_preview}</strong><div className="inline-pills"><span className={`pill ${job.status === "completed" ? "active" : ""}`}>{job.status}</span><span className="pill">{job.model || "-"}</span><span className="pill">{job.voice || "-"}</span><span className="pill">{formatMs(job.metrics.ttfa_ms)}</span></div><div className="button-row">{job.status === "completed" ? <button className="secondary-button" type="button" onClick={() => void handleLoadJobAudio(job.job_id)}>Audio</button> : null}<button className="ghost-button" type="button" onClick={() => void handleDeleteJob(job.job_id)}>{job.status === "completed" ? "Entfernen" : "Stornieren"}</button></div>{jobAudioUrls[job.job_id] ? <audio controls src={jobAudioUrls[job.job_id]} /> : null}</article>)}</div>
        </section>
      </section>
    </main>
  );
}
