import { useState, type ChangeEvent, type FormEvent } from 'react';
import { Badge } from '../components/Badge';
import { InfoTip } from '../components/InfoTip';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import type { VoiceProfile } from '../types';

interface VoicesViewProps {
  voices: VoiceProfile[];
  transcriptionHint: string;
  onCreateVoiceProfile: (input: {
    name: string;
    model: string;
    language: string;
    style: string;
    consent: boolean;
    sampleLabel: string;
    refText: string;
    sampleFileName: string;
    file: File | null;
  }) => Promise<void>;
  onTranscribeSample: (input: { file: File | null; fileName: string }) => Promise<{ transcription: string; voiceVector: number[] }>;
}

export function VoicesView({ voices, transcriptionHint, onCreateVoiceProfile, onTranscribeSample }: VoicesViewProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState('');
  const [profileName, setProfileName] = useState('New Voice');
  const [model, setModel] = useState('Qwen/Qwen3-TTS-12Hz-0.6B-Base');
  const [language, setLanguage] = useState('German');
  const [style, setStyle] = useState('Calm and practical');
  const [refText, setRefText] = useState('');
  const [consent, setConsent] = useState(true);
  const [draft, setDraft] = useState('No transcription yet.');
  const [vector, setVector] = useState<number[]>([]);
  const [status, setStatus] = useState('');

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setFileName(file?.name ?? '');
  }

  async function handleTranscribe() {
    if (!fileName.trim()) {
      setDraft('Choose a sample file first.');
      return;
    }

    setStatus('Requesting Whisper draft...');
    try {
      const result = await onTranscribeSample({ file: selectedFile, fileName });
      setDraft(result.transcription);
      setVector(result.voiceVector);
      setRefText(result.transcription);
      setStatus('Whisper draft loaded.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Whisper draft could not be created.');
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus('Saving voice profile...');
    try {
      await onCreateVoiceProfile({
        name: profileName,
        model,
        language,
        style,
        consent,
        sampleLabel: fileName || 'No file selected',
        refText,
        sampleFileName: fileName || 'sample.wav',
        file: selectedFile
      });
      setProfileName('New Voice');
      setSelectedFile(null);
      setFileName('');
      setRefText('');
      setStatus('Voice profile saved.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Voice profile could not be saved.');
    }
  }

  return (
    <div className="view-grid">
      <SectionHeader title="Voice Lab" subtitle="Review built-ins, then create or refine custom profiles with Whisper support." />
      <div className="two-column">
        <Panel title="Voice Library" subtitle="Built-in and custom profiles stay together so operators can route jobs quickly.">
          <div className="stack-list">
            {voices.length === 0 ? (
              <div className="empty-card">
                <strong>No voices available yet</strong>
                <p>Create a custom profile on the right or rely on built-in speakers from the synthesis view.</p>
              </div>
            ) : (
              voices.map((voice) => (
                <div key={voice.id} className="voice-card">
                  <div className="stack-row-meta">
                    <strong>{voice.name}</strong>
                    <Badge label={voice.kind} tone={voice.kind === 'custom' ? 'success' : 'neutral'} />
                  </div>
                  <p>{voice.style}</p>
                  <div className="stack-row-meta">
                    <span>{voice.language}</span>
                    <span>{voice.model}</span>
                    <span>{voice.sampleLabel}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </Panel>
        <Panel title="Clone Workflow" subtitle="Upload a sample, draft the transcript, then save the profile into the control room.">
          <form className="form-grid" onSubmit={handleSubmit}>
            <label>
              <span className="field-label">
                Sample upload
                <InfoTip text="Upload a clean reference clip for Whisper drafting and voice-profile creation. Short, dry samples usually work best." />
              </span>
              <input type="file" accept="audio/*" onChange={handleFileChange} />
            </label>
            <p className="inline-note">{fileName || 'No file selected yet.'}</p>
            <div className="form-row">
              <label>
                <span className="field-label">
                  Profile name
                  <InfoTip text="Operator-facing label used later in Base mode and in the voice library." />
                </span>
                <input value={profileName} onChange={(event) => setProfileName(event.target.value)} />
              </label>
              <label>
                <span className="field-label">
                  Target model
                  <InfoTip text="Base models consume saved custom profiles. Keep the target model aligned with the profile you intend to use later." />
                </span>
                <input value={model} onChange={(event) => setModel(event.target.value)} />
              </label>
            </div>
            <div className="form-row">
              <label>
                <span className="field-label">
                  Language
                  <InfoTip text="Human-readable language note for operators. The runtime uses this as a reference when the profile is reused." />
                </span>
                <input value={language} onChange={(event) => setLanguage(event.target.value)} />
              </label>
              <label>
                <span className="field-label">
                  Style
                  <InfoTip text="Short descriptor for how the sample should be remembered by operators, such as calm, bright, or practical." />
                </span>
                <input value={style} onChange={(event) => setStyle(event.target.value)} />
              </label>
            </div>
            <label>
              <span className="field-label">
                Reference text
                <InfoTip text="Transcript tied to the sample. Whisper can draft this automatically, and you can clean it up before saving the profile." />
              </span>
              <textarea value={refText} onChange={(event) => setRefText(event.target.value)} rows={5} />
            </label>
            <label className="toggle">
              <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
              <span>I confirm there is consent to use the sample.</span>
            </label>
            <div className="button-row">
              <button className="secondary-button" type="button" onClick={handleTranscribe}>
                Use Whisper Draft
              </button>
              <button className="primary-button" type="submit">
                Save Profile
              </button>
            </div>
            <p className="inline-note">{status || 'Short, clean reference clips work best for reliable cloning.'}</p>
          </form>
          <div className="callout subtle-callout">
            <strong>Whisper helper</strong>
            <p>{transcriptionHint}</p>
            <p className="inline-note">{draft}</p>
            <p className="inline-note">{vector.length ? `Vector: [${vector.map((entry) => entry.toFixed(2)).join(', ')}]` : 'No vector yet.'}</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}
