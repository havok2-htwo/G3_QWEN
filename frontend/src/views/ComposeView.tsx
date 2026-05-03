import { useEffect, useState, useTransition, type FormEvent } from 'react';
import { InfoTip } from '../components/InfoTip';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import type { ComposeRequest, ModelInfo, TaskType, VoiceProfile } from '../types';

interface ComposeViewProps {
  defaultModel: string;
  defaultVoice: string;
  models: ModelInfo[];
  voices: VoiceProfile[];
  builtInVoices: string[];
  onSubmit: (request: ComposeRequest) => Promise<void>;
}

/** Derive task type from a model id based on its suffix. */
function taskTypeFromModel(modelId: string): TaskType {
  if (modelId.endsWith('VoiceDesign')) return 'VoiceDesign';
  if (modelId.endsWith('Base')) return 'Base';
  return 'CustomVoice';
}

/** 0.6B CustomVoice models silently ignore instructions – flag it for the UX. */
function is06BModel(modelId: string): boolean {
  return modelId.includes('0.6B');
}

function buildInitialRequest(defaultModel: string, defaultVoice: string): ComposeRequest {
  return {
    input: 'Please wait a moment while the local synthesis pipeline warms up.',
    model: defaultModel,
    voice: defaultVoice,
    taskType: taskTypeFromModel(defaultModel),
    language: 'German',
    instructions: '',
    responseFormat: 'wav',
    speed: 1,
    stream: false,
    refText: '',
    refAudioLabel: '',
    xVectorOnlyMode: false
  };
}

export function ComposeView({ defaultModel, defaultVoice, models, voices, builtInVoices, onSubmit }: ComposeViewProps) {
  const [request, setRequest] = useState<ComposeRequest>(() =>
    buildInitialRequest(defaultModel, defaultVoice)
  );
  const [message, setMessage] = useState('Ready to queue a synthesis request.');
  const [isPending, startTransition] = useTransition();

  const taskType = taskTypeFromModel(request.model);
  const isCustomVoice = taskType === 'CustomVoice';
  const isVoiceDesign = taskType === 'VoiceDesign';
  const isBase = taskType === 'Base';
  const smallModel = is06BModel(request.model);
  const modelNote = isVoiceDesign
    ? 'VoiceDesign generates a voice from descriptive instructions instead of selecting a fixed speaker.'
    : isBase
      ? 'Base mode routes through a saved custom voice profile and can optionally override the stored reference text.'
      : smallModel
        ? '0.6B CustomVoice is optimized for speed. Instruction text is ignored by this model family.'
        : '1.7B CustomVoice supports extra style instructions at the cost of more GPU load.';

  // Voices available for the current task type
  const customProfiles = voices.filter((v) => v.kind === 'custom');
  const builtInOptions = builtInVoices.map((name) => ({ id: name.toLowerCase(), name }));

  // Autocorrect invalid voice selections when remounting or navigating back from history
  useEffect(() => {
    if (isBase) {
      const isValid = customProfiles.some((v) => v.name === request.voice || v.id === request.voice);
      if (!isValid && customProfiles.length > 0) {
        setRequest((current) => ({ ...current, voice: customProfiles[0].name }));
      }
    } else if (isCustomVoice) {
      const builtInNames = builtInVoices.map((n) => n.toLowerCase());
      if (!builtInNames.includes(request.voice.toLowerCase()) && builtInVoices.length > 0) {
        setRequest((current) => ({ ...current, voice: builtInVoices[0] }));
      }
    }
  }, [isBase, isCustomVoice, request.voice, customProfiles, builtInVoices]);

  function patch<K extends keyof ComposeRequest>(key: K, value: ComposeRequest[K]) {
    setRequest((current) => ({ ...current, [key]: value }));
  }

  function handleModelChange(modelId: string) {
    const newTaskType = taskTypeFromModel(modelId);
    setRequest((current) => {
      // Reset voice to a sensible default when switching modes
      let newVoice = current.voice;
      if (newTaskType === 'CustomVoice') {
        // Pick first built-in speaker if current voice is a custom profile name
        const builtInNames = builtInVoices.map((n) => n.toLowerCase());
        if (!builtInNames.includes(current.voice.toLowerCase())) {
          newVoice = builtInVoices[0] ?? '';
        }
      } else if (newTaskType === 'Base') {
        // Pick first custom profile if available
        if (customProfiles.length > 0) {
          newVoice = customProfiles[0].name;
        }
      } else {
        newVoice = '';
      }
      return { ...current, model: modelId, taskType: newTaskType, voice: newVoice };
    });
  }

  function handleStreamToggle(enabled: boolean) {
    setRequest((current) => ({
      ...current,
      stream: enabled,
      responseFormat: enabled ? 'pcm' : current.responseFormat === 'pcm' ? 'wav' : current.responseFormat,
      speed: enabled ? 1 : current.speed
    }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage('Queueing request...');
    startTransition(() => {
      void onSubmit(request)
        .then(() => setMessage('Queued successfully. Check History for the live record.'))
        .catch((error) => setMessage(error instanceof Error ? error.message : 'The synthesis request could not be queued.'));
    });
  }

  return (
    <div className="view-grid">
      <SectionHeader
        title="Synthesis"
        subtitle="Prepare a low-latency synthesis request with clearer model guidance, voice routing, and payload visibility."
      />
      <div className="two-column">
        <Panel title="Request Form" subtitle="Fields adapt automatically to the selected model family and synthesis mode.">
          <form className="form-grid" onSubmit={handleSubmit}>

            {/* ----- Text input ----- */}
            <label>
              <span className="field-label">
                Text
                <InfoTip text="Main synthesis payload sent to the QWEN runtime. Longer prompts increase total generation time and may influence batching behavior." />
              </span>
              <textarea value={request.input} onChange={(e) => patch('input', e.target.value)} rows={8} />
            </label>

            {/* ----- Model ----- */}
            <div className="form-row">
              <label>
                <span className="field-label">
                  Model
                  <InfoTip text="Select the local QWEN model variant. CustomVoice uses built-in speakers, Base uses cloned profiles, and VoiceDesign generates from style instructions." />
                </span>
                <select value={request.model} onChange={(e) => handleModelChange(e.target.value)}>
                  {models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="field-label">
                  Task type
                  <InfoTip text="Derived automatically from the selected model family so operators can confirm the request route at a glance." />
                </span>
                <input value={taskType} disabled style={{ opacity: 0.6 }} />
              </label>
            </div>

            {/* ----- Voice — only for CustomVoice and Base ----- */}
            {isCustomVoice && (
              <label>
                <span className="field-label">
                  Speaker (built-in)
                  <InfoTip text="Built-in speakers are available for CustomVoice models and do not require an uploaded voice profile." />
                </span>
                <select value={request.voice} onChange={(e) => patch('voice', e.target.value)}>
                  {builtInOptions.map((v) => (
                    <option key={v.id} value={v.name}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {isBase && (
              <label>
                <span className="field-label">
                  Voice profile (custom)
                  <InfoTip text="Base models use stored custom voice profiles created in the Voice Lab." />
                </span>
                {customProfiles.length === 0 ? (
                  <p className="inline-note" style={{ margin: 0 }}>
                    No custom profiles yet. Create one in the <strong>Voice Lab</strong> tab first.
                  </p>
                ) : (
                  <select value={request.voice} onChange={(e) => patch('voice', e.target.value)}>
                    {customProfiles.map((v) => (
                      <option key={v.id} value={v.name}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                )}
              </label>
            )}

            {isVoiceDesign && (
              <p className="inline-note">
                VoiceDesign mode: describe the target voice in the <strong>Instructions</strong> field below. No fixed speaker is selected.
              </p>
            )}

            {/* ----- Language + response format ----- */}
            <div className="form-row">
              <label>
                <span className="field-label">
                  Language
                  <InfoTip text="Language hint forwarded to the runtime. Keep it aligned with the target text when you want stable pronunciation behavior." />
                </span>
                <input value={request.language} onChange={(e) => patch('language', e.target.value)} />
              </label>
              <label>
                <span className="field-label">
                  Response format
                  <InfoTip text="Streaming requires raw PCM. File-based requests can return WAV, MP3, or PCM depending on the backend route." />
                </span>
                <select
                  value={request.responseFormat}
                  onChange={(e) => patch('responseFormat', e.target.value as ComposeRequest['responseFormat'])}
                  disabled={request.stream}
                >
                  <option value="mp3">mp3</option>
                  <option value="wav">wav</option>
                  <option value="pcm">pcm</option>
                </select>
              </label>
              {!request.stream && (
                <label>
                  <span className="field-label">
                    Speed
                    <InfoTip text="Playback speed hint for non-streaming generation. Streaming is fixed at 1.0 by the current backend." />
                  </span>
                  <input
                    type="number"
                    min="0.5"
                    max="2"
                    step="0.05"
                    value={request.speed}
                    onChange={(e) => patch('speed', Number(e.target.value))}
                  />
                </label>
              )}
            </div>

            {/* ----- Instructions — VoiceDesign always, CustomVoice 1.7B only ----- */}
            {(isVoiceDesign || (isCustomVoice && !smallModel)) && (
              <label>
                <span className="field-label">
                  Instructions{isVoiceDesign ? ' (voice description)' : ''}
                  <InfoTip text="Use this field for style notes or a voice description. Smaller 0.6B CustomVoice models ignore instructions." />
                </span>
                <textarea
                  value={request.instructions}
                  onChange={(e) => patch('instructions', e.target.value)}
                  rows={3}
                  placeholder={isVoiceDesign ? 'e.g. A calm, deep male narrator voice with a slight British accent.' : 'Optional style instructions.'}
                />
              </label>
            )}

            {/* ----- Base / voice-clone specific fields ----- */}
            {isBase && (
              <div className="form-row">
                <label>
                  <span className="field-label">
                    Reference text override
                    <InfoTip text="Optional override for the saved profile transcript when you want to supply a different reference text at synthesis time." />
                  </span>
                  <input
                    value={request.refText}
                    onChange={(e) => patch('refText', e.target.value)}
                    placeholder="Leave empty to use the saved profile's ref text"
                  />
                </label>
                <label className="toggle" style={{ alignSelf: 'flex-end', paddingBottom: '6px' }}>
                  <input
                    type="checkbox"
                    checked={request.xVectorOnlyMode}
                    onChange={(e) => patch('xVectorOnlyMode', e.target.checked)}
                  />
                  <span>X-vector only</span>
                </label>
              </div>
            )}

            {/* ----- Stream toggle ----- */}
            <div className="toggle-row">
              <label className="toggle">
                <input type="checkbox" checked={request.stream} onChange={(e) => handleStreamToggle(e.target.checked)} />
                <span>Stream PCM</span>
              </label>
            </div>
            {request.stream && <p className="inline-note">Streaming uses raw PCM and keeps speed fixed at 1.0.</p>}
            <p className="inline-note">{modelNote}</p>

            <button className="primary-button" type="submit" disabled={isPending}>
              Queue Synthesis
            </button>
            <p className="inline-note">{message}</p>
          </form>
        </Panel>

        <Panel title="Operator Summary" subtitle="Sanity-check the route, output mode, and exact backend payload before queuing the job.">
          <div className="metric-grid compact summary-grid">
            <div className="metric-card">
              <span>Model route</span>
              <strong>{taskType}</strong>
            </div>
            <div className="metric-card">
              <span>Voice target</span>
              <strong>{request.voice || 'Prompt-defined'}</strong>
            </div>
            <div className="metric-card">
              <span>Output</span>
              <strong>{request.stream ? 'PCM stream' : request.responseFormat}</strong>
            </div>
            <div className="metric-card">
              <span>Speed</span>
              <strong>{request.stream ? '1.0 fixed' : request.speed.toFixed(2)}</strong>
            </div>
          </div>
          <div className="callout subtle-callout">
            <strong>Current mode</strong>
            <p>{modelNote}</p>
          </div>
          <pre className="code-preview">{JSON.stringify({ ...request, taskType }, null, 2)}</pre>
        </Panel>
      </div>
    </div>
  );
}
