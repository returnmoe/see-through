import type { MemoryProfile } from './types';

export type SelectedMemoryProfile = Exclude<MemoryProfile, 'auto'>;
export type VramFit = 'unknown' | 'unsupported' | 'likely-too-small' | 'tight' | 'roomy';

export interface VramEstimate {
  profile: SelectedMemoryProfile;
  profileIsAssumed: boolean;
  lowGib: number;
  highGib: number;
  recommendedGib: number;
  extrapolated: boolean;
  fit: VramFit;
}

const ANCHORS: Record<SelectedMemoryProfile, { low: number; high: number }> = {
  bf16: { low: 12, high: 16 },
  'group-offload': { low: 9, high: 11 },
  nf4: { low: 7, high: 9 },
  blockswap: { low: 7, high: 9 },
};

export function resolveMemoryProfile(
  requested: MemoryProfile,
  memoryMib?: number,
): { profile: SelectedMemoryProfile; assumed: boolean; supported: boolean } {
  if (requested !== 'auto') {
    return { profile: requested, assumed: false, supported: true };
  }

  if (!memoryMib) {
    return { profile: 'bf16', assumed: true, supported: true };
  }
  if (memoryMib >= 18 * 1024) {
    return { profile: 'bf16', assumed: false, supported: true };
  }
  if (memoryMib >= 11 * 1024) {
    return { profile: 'group-offload', assumed: false, supported: true };
  }
  if (memoryMib >= 8 * 1024) {
    return { profile: 'nf4', assumed: false, supported: true };
  }
  return { profile: 'nf4', assumed: false, supported: false };
}

export function estimateVram(
  requested: MemoryProfile,
  resolution: number,
  depthResolution: number,
  memoryMib?: number,
): VramEstimate {
  const resolved = resolveMemoryProfile(requested, memoryMib);
  const anchor = ANCHORS[resolved.profile];

  // The two diffusion stages run sequentially. Treat the larger normalized
  // square-pixel workload as the planning factor instead of adding both peaks.
  const layerFactor = (resolution / 1280) ** 2;
  const depthFactor = (depthResolution / 768) ** 2;
  const pixelFactor = Math.max(layerFactor, depthFactor);
  const lowGib = Math.ceil(Math.max(8, anchor.low + 4 * (pixelFactor - 1)));
  const highGib = Math.ceil(Math.max(8, anchor.high + 6 * (pixelFactor - 1)));
  const extrapolated = resolution > 1280 || depthResolution > 768;
  const headroomGib = extrapolated ? Math.max(2, Math.ceil(highGib * 0.1)) : 2;
  const recommendedGib = highGib + headroomGib;
  const memoryGib = memoryMib ? memoryMib / 1024 : undefined;

  let fit: VramFit = 'unknown';
  if (!resolved.supported) {
    fit = 'unsupported';
  } else if (memoryGib !== undefined && memoryGib < lowGib) {
    fit = 'likely-too-small';
  } else if (memoryGib !== undefined && memoryGib < recommendedGib) {
    fit = 'tight';
  } else if (memoryGib !== undefined) {
    fit = 'roomy';
  }

  return {
    profile: resolved.profile,
    profileIsAssumed: resolved.assumed,
    lowGib,
    highGib,
    recommendedGib,
    extrapolated,
    fit,
  };
}

export function formatVramRange(estimate: VramEstimate): string {
  return estimate.lowGib === estimate.highGib
    ? `~${estimate.lowGib} GiB`
    : `~${estimate.lowGib}–${estimate.highGib} GiB`;
}

export function vramFitMessage(estimate: VramEstimate, memoryMib?: number): string {
  const detected = memoryMib ? `${Math.round(memoryMib / 1024)} GiB` : '';
  switch (estimate.fit) {
    case 'unsupported':
      return `${detected} detected; Auto requires at least 8 GiB on one GPU.`;
    case 'likely-too-small':
      return `${detected} detected; this job is likely to exceed the active GPU.`;
    case 'tight':
      return `${detected} detected; fit is tight or uncertain. Validate a representative run.`;
    case 'roomy':
      return `${detected} detected; capacity appears sufficient, but the estimate is not a guarantee.`;
    default:
      return estimate.profileIsAssumed
        ? 'GPU memory capacity was not detected. Until it is known, Auto estimates the highest-memory BF16 profile.'
        : 'GPU capacity is not available for comparison.';
  }
}
