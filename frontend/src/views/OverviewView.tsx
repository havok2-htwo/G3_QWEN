import { Badge } from '../components/Badge';
import { MetricCard } from '../components/MetricCard';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import { Sparkline } from '../components/Sparkline';
import type { JobRecord, ModelInfo, OverviewHistoryPoint, OverviewStats, ServerSettings } from '../types';
import { formatDate, formatMs, formatRealtime, formatSeconds } from '../lib/format';

interface OverviewViewProps {
  stats: OverviewStats;
  history: OverviewHistoryPoint[];
  jobs: JobRecord[];
  models: ModelInfo[];
  settings: ServerSettings;
}

function formatGpuMemory(stats: OverviewStats) {
  if (!stats.gpuMemoryTotalMb) {
    return 'Waiting for GPU stats';
  }

  return `${stats.gpuMemoryUsedMb} / ${stats.gpuMemoryTotalMb} MB`;
}

export function OverviewView({ stats, history, jobs, models, settings }: OverviewViewProps) {
  const latestJobs = jobs.slice(0, 5);
  const warmModels = models.filter((model) => model.status === 'warm').length;
  const queuedJobs = jobs.filter((job) => ['queued', 'warming', 'running', 'streaming', 'cancelling'].includes(job.status)).length;

  return (
    <div className="view-grid">
      <SectionHeader
        title="Overview"
        subtitle="Track throughput, GPU pressure, queue movement, and model readiness from one operator surface."
      />

      <div className="metric-grid">
        <MetricCard
          label="Queue Depth"
          value={String(stats.queueDepth)}
          hint={`Worker ${stats.workerState}`}
          tooltip="Number of jobs currently waiting or being handled by the synthesis worker."
          accent="steel"
        />
        <MetricCard
          label="TTFA Avg"
          value={formatMs(stats.ttfaMsAvg)}
          hint="Time to first audio"
          tooltip="Average delay until the backend emits the first audible output chunk."
          accent="gold"
        />
        <MetricCard
          label="Queue Wait"
          value={formatMs(stats.queueWaitMsAvg)}
          hint="Rolling wait time"
          tooltip="Average time requests spend waiting before the worker actually starts generating audio."
          accent="teal"
        />
        <MetricCard
          label="Realtime Factor"
          value={formatRealtime(stats.realtimeXAvg)}
          hint="Higher is faster"
          tooltip="Generation speed relative to audio duration. Values above 1.0x mean faster-than-realtime synthesis."
          accent="rose"
        />
        <MetricCard
          label="GPU Memory"
          value={formatGpuMemory(stats)}
          hint={`Utilization ${Math.round(stats.gpuUtilizationPct)}%`}
          tooltip="Current GPU memory footprint and utilization reported by the runtime snapshot."
          accent="steel"
        />
        <MetricCard
          label="Audio Processed"
          value={formatSeconds(stats.audioSecondsTotal)}
          hint={`${stats.jobsTotal} total jobs`}
          tooltip="Total amount of generated audio tracked by the backend across recorded jobs."
          accent="gold"
        />
      </div>

      <div className="two-column">
        <Panel
          title="Performance Graph"
          subtitle="A rolling local history keeps the same kind of fast visual performance readout that operators rely on in TADA."
        >
          <div className="sparkline-grid">
            <Sparkline
              title="Throughput"
              subtitle="Realtime factor vs queue depth"
              primaryLabel="Realtime"
              primaryValues={history.map((entry) => entry.realtimeXAvg)}
              primaryColor="#95f2c7"
              primaryFormatter={(value) => formatRealtime(value)}
              secondaryLabel="Queue"
              secondaryValues={history.map((entry) => entry.queueDepth)}
              secondaryColor="#ffc16c"
            />
            <Sparkline
              title="GPU Pressure"
              subtitle="Utilization vs memory used"
              primaryLabel="GPU"
              primaryValues={history.map((entry) => entry.gpuUtilizationPct)}
              primaryColor="#ffc16c"
              primaryFormatter={(value) => `${Math.round(value)}%`}
              secondaryLabel="Memory"
              secondaryValues={history.map((entry) => entry.gpuMemoryUsedMb)}
              secondaryColor="#ff8d94"
              secondaryFormatter={(value) => `${Math.round(value)} MB`}
            />
          </div>
          <div className="insight-row">
            <div className="insight-card">
              <span>Worker State</span>
              <strong>{stats.workerState}</strong>
              <p>{queuedJobs} active or queued job{queuedJobs === 1 ? '' : 's'} currently tracked.</p>
            </div>
            <div className="insight-card">
              <span>Thermals</span>
              <strong>{stats.gpuTemperatureC ? `${Math.round(stats.gpuTemperatureC)} C` : 'No sensor data'}</strong>
              <p>Live temperature reporting depends on the connected runtime and GPU driver support.</p>
            </div>
          </div>
        </Panel>

        <Panel title="Model Overview" subtitle="Keep the active routing and model footprint predictable for operators.">
          <div className="stack-list">
            <div className="stack-row">
              <div>
                <strong>Active model</strong>
                <span>{stats.activeModel || settings.defaultModel}</span>
              </div>
              <div className="stack-row-meta">
                <Badge label={`${warmModels} warm`} tone={warmModels > 0 ? 'success' : 'warning'} />
                <Badge label={settings.runtimeBackend} tone="neutral" />
              </div>
            </div>
            <div className="stack-row">
              <div>
                <strong>Model directory</strong>
                <span className="mono">{settings.modelDirectory}</span>
              </div>
              <div className="stack-row-meta">
                <span>{settings.preferredDevice}</span>
                <span>{settings.torchDtype}</span>
              </div>
            </div>
            {models.map((model) => (
              <div key={model.id} className="stack-row">
                <div>
                  <strong>{model.label}</strong>
                  <span>{model.id}</span>
                </div>
                <div className="stack-row-meta">
                  <Badge label={model.status} tone={model.status === 'warm' ? 'success' : model.status === 'warming' ? 'warning' : 'neutral'} />
                  <span>{model.taskTypes.join(' / ')}</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="Recent Activity" subtitle="History and queue samples share one card so recent synthesis work is easier to scan.">
        <div className="job-mini-list">
          {latestJobs.length === 0 ? (
            <div className="empty-card">
              <strong>No synthesis jobs recorded yet</strong>
              <p>The first request will appear here together with runtime, queue position, and audio status.</p>
            </div>
          ) : (
            latestJobs.map((job) => (
              <article key={job.id} className="job-card">
                <div className="job-mini-head">
                  <div>
                    <strong>{job.voice || 'Unknown voice'}</strong>
                    <span>{job.model}</span>
                  </div>
                  <Badge label={job.status} tone={job.status === 'completed' ? 'success' : job.status === 'failed' ? 'danger' : 'warning'} />
                </div>
                <p>{job.input}</p>
                <div className="stack-row-meta">
                  <span>{formatDate(job.updatedAt)}</span>
                  <span>{formatMs(job.metrics?.ttfaMs ?? job.etaMs)}</span>
                  <span>{job.metrics ? formatRealtime(job.metrics.realtimeX) : 'Pending'}</span>
                </div>
              </article>
            ))
          )}
        </div>
      </Panel>
    </div>
  );
}
