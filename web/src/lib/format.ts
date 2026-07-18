import type { Artifact, Job } from './types';

export function formatBytes(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) return '—';
  if (value < 1024) return `${value} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let amount = value / 1024;
  let unit = units[0];
  for (let index = 1; amount >= 1024 && index < units.length; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
}

export function formatMemory(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) return 'Unknown VRAM';
  return `${(value / 1024).toFixed(1)} GiB`;
}

export function formatTime(value?: string): string {
  if (!value) return 'Just now';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Unknown time';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function jobLabel(job: Job): string {
  return job.input_name || job.original_filename || `Run ${job.id.slice(0, 8)}`;
}

export function jobProgress(job: Job): number {
  const progress = job.progress ?? (job.status === 'completed' ? 100 : 0);
  return Math.min(100, Math.max(0, progress <= 1 ? progress * 100 : progress));
}

export function artifactUrl(jobId: string, artifact: Artifact): string {
  const artifactId = artifact.id || artifact.name;
  return artifact.url || `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}`;
}

export function normalizeArtifacts(job?: Job): Artifact[] {
  if (!job) return [];
  if (Array.isArray(job.artifacts)) return job.artifacts;
  if (!job.artifact_manifest) return [];
  return Object.entries(job.artifact_manifest).map(([name, value]) =>
    typeof value === 'string' ? { name, url: value } : { name, ...value },
  );
}

export function logText(job?: Job): string {
  const logs = job?.logs ?? job?.log_tail;
  if (!logs) return 'Waiting for runtime output…';
  return Array.isArray(logs) ? logs.join('\n') : logs;
}
