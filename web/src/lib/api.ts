import type { ApiEvent, Job, JobOptions, SystemStatus } from './types';

const clientHeader = { 'X-See-Through-Client': 'web' };
type ApiJob = Omit<Job, 'status'> & { status?: Job['status'] };

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...clientHeader,
      ...init.headers,
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string; message?: string };
      message = body.detail || body.message || message;
    } catch {
      // Non-JSON errors retain the safe status message.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function normalizeJob(job: ApiJob): Job {
  const status = job.status || job.state || 'failed';
  const artifacts = (job.artifacts ?? []).map((artifact) => ({
    ...artifact,
    size: artifact.size ?? artifact.size_bytes,
    mime_type: artifact.mime_type ?? artifact.content_type,
    url:
      artifact.url ||
      (artifact.id
        ? `/api/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.id)}`
        : undefined),
  }));
  const reconstruction = artifacts.find((artifact) => artifact.kind === 'reconstruction');
  return {
    ...job,
    status,
    state: job.state ?? status,
    input_name: job.input_name ?? job.original_filename,
    logs: job.logs ?? job.log_tail,
    config: {
      profile: job.config?.profile ?? job.profile_requested,
      resolution: job.config?.resolution ?? job.resolution,
      seed: job.config?.seed ?? job.seed ?? 42,
      steps: job.config?.steps ?? job.steps ?? 30,
      depth_resolution: job.config?.depth_resolution ?? job.depth_resolution ?? 768,
      tblr_split: job.config?.tblr_split ?? job.tblr_split ?? false,
      ...job.config,
    },
    artifacts,
    reconstruction_url: job.reconstruction_url ?? reconstruction?.url,
  };
}

export function getSystem(): Promise<SystemStatus> {
  return request<SystemStatus>('/api/system');
}

export async function getJobs(): Promise<Job[]> {
  const response = await request<ApiJob[]>('/api/jobs');
  return response.map(normalizeJob);
}

export async function createJob(file: File, options: JobOptions): Promise<Job> {
  const body = new FormData();
  body.append('file', file, file.name);
  body.append('profile', options.profile);
  body.append('resolution', String(options.resolution));
  body.append('seed', String(options.seed));
  body.append('steps', String(options.steps));
  body.append('depth_resolution', String(options.depth_resolution));
  body.append('tblr_split', String(options.tblr_split));
  return normalizeJob(await request<ApiJob>('/api/jobs', { method: 'POST', body }));
}

export async function cancelJob(jobId: string): Promise<Job> {
  return normalizeJob(
    await request<ApiJob>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  );
}

export function deleteJob(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
}

export function prefetchModels(
  selection: 'auto' | 'bf16' | 'nf4' = 'auto',
): Promise<{ selection: string; status: string }> {
  return request<{ selection: string; status: string }>('/api/models/prefetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selection }),
  });
}

export function connectEvents(
  onEvent: (event: ApiEvent) => void,
  onConnection: (connected: boolean) => void,
): () => void {
  const source = new EventSource('/api/events');
  source.onopen = () => onConnection(true);
  source.onerror = () => onConnection(false);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as ApiEvent);
    } catch {
      onEvent({ type: 'invalidate' });
    }
  };
  const namedEvents = ['snapshot', 'job', 'models', 'job_deleted'];
  const listener = (message: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(message.data) as ApiEvent);
    } catch {
      onEvent({ type: message.type });
    }
  };
  namedEvents.forEach((eventName) => source.addEventListener(eventName, listener as EventListener));
  return () => source.close();
}
