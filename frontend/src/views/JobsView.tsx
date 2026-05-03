import { useEffect, useRef, useState } from 'react';
import { Badge } from '../components/Badge';
import { MetricCard } from '../components/MetricCard';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import type { JobRecord, JobStatus } from '../types';
import { formatDate, formatMs, formatPercent } from '../lib/format';

interface JobsViewProps {
  jobs: JobRecord[];
  onCancelJob?: (id: string) => Promise<void> | void;
  onLoadJobAudio?: (id: string) => Promise<Blob>;
}

function canCancel(status: JobStatus) {
  return status === 'queued' || status === 'warming' || status === 'running' || status === 'streaming' || status === 'cancelling';
}

function timingSummary(job: JobRecord) {
  const runtimeMs = job.metrics?.jobWallMs ?? 0;

  if (job.status === 'completed' && runtimeMs > 0) {
    return {
      primary: formatMs(runtimeMs),
      secondary: 'Completed runtime'
    };
  }

  if (job.status === 'cancelled' && runtimeMs > 0) {
    return {
      primary: formatMs(runtimeMs),
      secondary: 'Cancelled after'
    };
  }

  if (job.status === 'failed' && runtimeMs > 0) {
    return {
      primary: formatMs(runtimeMs),
      secondary: 'Failed after'
    };
  }

  if (job.status === 'cancelling') {
    return {
      primary: formatMs(Math.max(job.etaMs, runtimeMs)),
      secondary: 'Stopping now'
    };
  }

  if (job.queuePosition > 0) {
    return {
      primary: formatMs(job.etaMs),
      secondary: `Queue #${job.queuePosition}`
    };
  }

  return {
    primary: formatMs(job.etaMs),
    secondary: 'In progress'
  };
}

export function JobsView({ jobs, onCancelJob, onLoadJobAudio }: JobsViewProps) {
  const [audioSources, setAudioSources] = useState<Record<string, string>>({});
  const [loadingAudioId, setLoadingAudioId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState('');
  const audioSourcesRef = useRef<Record<string, string>>({});
  const activeJobs = jobs.filter((job) => canCancel(job.status)).length;
  const completedJobs = jobs.filter((job) => job.status === 'completed').length;
  const failedJobs = jobs.filter((job) => job.status === 'failed').length;
  const avgTtfa =
    jobs.filter((job) => job.metrics?.ttfaMs).reduce((sum, job) => sum + (job.metrics?.ttfaMs ?? 0), 0) /
    Math.max(1, jobs.filter((job) => job.metrics?.ttfaMs).length);

  useEffect(() => {
    audioSourcesRef.current = audioSources;
  }, [audioSources]);

  useEffect(() => {
    return () => {
      for (const url of Object.values(audioSourcesRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  async function handleLoadAudio(jobId: string) {
    if (!onLoadJobAudio || audioSources[jobId]) {
      return;
    }

    setLoadingAudioId(jobId);
    try {
      const blob = await onLoadJobAudio(jobId);
      const objectUrl = URL.createObjectURL(blob);
      setAudioSources((current) => {
        const previousUrl = current[jobId];
        if (previousUrl) {
          URL.revokeObjectURL(previousUrl);
        }
        return {
          ...current,
          [jobId]: objectUrl
        };
      });
      setLoadError('');
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'The finished audio could not be loaded.');
    } finally {
      setLoadingAudioId((current) => (current === jobId ? null : current));
    }
  }

  return (
    <div className="view-grid">
      <SectionHeader
        title="History"
        subtitle="Review queued, running, and completed synthesis work in a single operator-facing history table."
      />

      <div className="metric-grid compact">
        <MetricCard
          label="Active queue"
          value={String(activeJobs)}
          hint="Queued, running, or cancelling"
          tooltip="Count of jobs that are still occupying queue or worker capacity right now."
          accent="steel"
        />
        <MetricCard
          label="Completed"
          value={String(completedJobs)}
          hint="Jobs with audio ready"
          tooltip="Finished jobs whose output audio is ready to inspect or play back."
          accent="teal"
        />
        <MetricCard
          label="Failed"
          value={String(failedJobs)}
          hint="Jobs needing operator review"
          tooltip="Requests that ended in an error and may need retry, prompt cleanup, or runtime investigation."
          accent="rose"
        />
        <MetricCard
          label="Average TTFA"
          value={formatMs(avgTtfa)}
          hint="Across measured jobs"
          tooltip="Average time to first audio across jobs that already reported timing data."
          accent="gold"
        />
      </div>

      <Panel title="Queue and History" subtitle="Cancel active work, inspect runtime, and listen to finished output in place.">
        {loadError ? <p className="inline-note">{loadError}</p> : null}
        {jobs.length === 0 ? (
          <div className="empty-card">
            <strong>No job history yet</strong>
            <p>The first synthesis request will appear here with status, progress, timing, and inline audio playback.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Voice</th>
                  <th>Model</th>
                  <th>Request</th>
                  <th>Timing</th>
                  <th>Progress</th>
                  <th>Audio</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const timing = timingSummary(job);
                  const audioReady = job.status === 'completed';
                  const audioSource = audioSources[job.id];
                  const showCancel = Boolean(onCancelJob) && canCancel(job.status);

                  return (
                    <tr key={job.id}>
                      <td>
                        <div className="status-cell-stack">
                          <div className="stack-row-meta">
                            <Badge label={job.status} tone={job.status === 'completed' ? 'success' : job.status === 'failed' ? 'danger' : 'warning'} />
                            {showCancel && (
                              <button
                                className="secondary-button tiny-button"
                                disabled={job.status === 'cancelling'}
                                onClick={() => {
                                  if (job.status !== 'cancelling') {
                                    void onCancelJob?.(job.id);
                                  }
                                }}
                              >
                                {job.status === 'cancelling' ? 'Cancelling...' : 'Cancel'}
                              </button>
                            )}
                          </div>
                          {job.errorMessage ? <span className="muted small-copy">{job.errorMessage}</span> : null}
                        </div>
                      </td>
                      <td>
                        <strong>{job.voice}</strong>
                        <span>{job.taskType}</span>
                      </td>
                      <td>{job.model}</td>
                      <td>
                        <div className="text-preview">
                          <p>{job.input}</p>
                          <span>{job.instructions || 'No extra instructions'}</span>
                        </div>
                      </td>
                      <td>
                        <div className="timing-cell">
                          <strong>{timing.primary}</strong>
                          <span>{timing.secondary}</span>
                          {job.metrics?.queueWaitMs ? <span className="muted small-copy">Wait {formatMs(job.metrics.queueWaitMs)}</span> : null}
                        </div>
                      </td>
                      <td>
                        <div className="progress-cell">
                          <div className="progress-track">
                            <div className="progress-fill" style={{ width: `${job.progress}%` }} />
                          </div>
                          <span>{formatPercent(job.progress)}</span>
                        </div>
                      </td>
                      <td>
                        {audioReady ? (
                          audioSource ? (
                            <audio controls preload="none" src={audioSource} style={{ width: '220px', maxWidth: '100%' }} />
                          ) : (
                            <button
                              className="secondary-button"
                              style={{ minHeight: '32px', padding: '6px 12px' }}
                              disabled={!onLoadJobAudio || loadingAudioId === job.id}
                              onClick={() => {
                                void handleLoadAudio(job.id);
                              }}
                            >
                              {loadingAudioId === job.id ? 'Loading...' : 'Listen'}
                            </button>
                          )
                        ) : (
                          <span className="muted">Available when complete</span>
                        )}
                      </td>
                      <td>{formatDate(job.updatedAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
