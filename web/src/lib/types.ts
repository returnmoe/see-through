export type JobStatus =
  | 'queued'
  | 'waiting_for_models'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

export type MemoryProfile = 'auto' | 'bf16' | 'group-offload' | 'nf4' | 'blockswap';
export type ModelBundle = 'bf16' | 'nf4';
export type ModelState = 'not_downloaded' | 'downloading' | 'ready' | 'failed' | 'unknown';
export type Resolution = number;

export interface JobOptions {
  profile: MemoryProfile;
  resolution: Resolution;
  seed: number;
  steps: number;
  depth_resolution: Resolution;
}

export interface Artifact {
  id?: string;
  name: string;
  label?: string;
  url?: string;
  kind?: 'image' | 'psd' | 'archive' | 'metadata' | 'log' | string;
  mime_type?: string;
  content_type?: string;
  size?: number;
  size_bytes?: number;
}

export interface Job {
  id: string;
  status: JobStatus;
  state?: JobStatus;
  phase?: string;
  progress?: number;
  created_at?: string;
  updated_at?: string;
  queue_position?: number;
  input_name?: string;
  original_filename?: string;
  original_url?: string;
  reconstruction_url?: string;
  preview_url?: string;
  error?: string;
  logs?: string[] | string;
  log_tail?: string[];
  profile_requested?: MemoryProfile;
  profile_selected?: MemoryProfile;
  resolution?: Resolution;
  seed?: number;
  steps?: number;
  depth_resolution?: Resolution;
  config?: {
    profile?: MemoryProfile;
    selected_profile?: MemoryProfile;
    resolution?: Resolution;
    seed?: number;
    steps?: number;
    depth_resolution?: Resolution;
  };
  artifacts?: Artifact[];
  artifact_manifest?: Record<string, Omit<Artifact, 'name'> | string>;
}

export interface SystemStatus {
  gpu?: {
    available?: boolean;
    name?: string;
    memory_mib?: number;
    memory_gib?: number;
    driver_version?: string;
    count?: number;
    devices?: Array<{
      index?: number;
      name?: string;
      memory_mib?: number;
      memory_gib?: number;
      driver_version?: string;
    }>;
  };
  models?: {
    cache_dir?: string;
    disk_free_bytes?: number;
    reserve_bytes?: number;
    bundles?: Record<
      ModelBundle,
      {
        state?: ModelState;
        error?: string | null;
        updated_at?: string | null;
        expected_size_bytes?: number;
      }
    >;
  };
  queue?: {
    counts?: Partial<Record<JobStatus, number>>;
    running_job_id?: string | null;
  };
  storage?: {
    data_dir?: string;
    disk_free_bytes?: number;
    disk_total_bytes?: number;
  };
  listener?: {
    host?: string;
    port?: number;
    loopback_only?: boolean;
  };
  version?: string;
}

export interface ApiEvent {
  type?: string;
  job_id?: string;
  job?: Job;
  jobs?: Job[];
  models?: SystemStatus['models'];
  bundle?: string;
  state?: string;
  data?: unknown;
}
