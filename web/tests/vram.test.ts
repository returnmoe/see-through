import { describe, expect, it } from 'vitest';
import {
  estimateVram,
  formatVramRange,
  resolveMemoryProfile,
  vramFitMessage,
} from '../src/lib/vram';

describe('VRAM planning estimate', () => {
  it('matches the server Auto thresholds', () => {
    expect(resolveMemoryProfile('auto', 24 * 1024).profile).toBe('bf16');
    expect(resolveMemoryProfile('auto', 16 * 1024).profile).toBe('group-offload');
    expect(resolveMemoryProfile('auto', 10 * 1024).profile).toBe('nf4');
    expect(resolveMemoryProfile('auto', 7 * 1024).supported).toBe(false);
  });

  it('preserves the published 1280 planning anchors', () => {
    expect(formatVramRange(estimateVram('bf16', 1280, 768))).toBe('~12–16 GiB');
    expect(formatVramRange(estimateVram('group-offload', 1280, 768))).toBe('~9–11 GiB');
    expect(formatVramRange(estimateVram('nf4', 1280, 768))).toBe('~8–9 GiB');
  });

  it('scales the estimate by the dominant normalized pixel workload', () => {
    const layerHeavy = estimateVram('bf16', 4096, 768);
    expect([layerHeavy.lowGib, layerHeavy.highGib]).toEqual([49, 72]);
    expect(layerHeavy.extrapolated).toBe(true);

    const depthHeavy = estimateVram('bf16', 1280, 1536);
    expect([depthHeavy.lowGib, depthHeavy.highGib]).toEqual([24, 34]);
    expect(depthHeavy.recommendedGib).toBe(38);
  });

  it('compares against one detected GPU with safety headroom', () => {
    expect(estimateVram('auto', 1280, 768, 24 * 1024).fit).toBe('roomy');
    expect(estimateVram('bf16', 2048, 768, 24 * 1024).fit).toBe('tight');
    expect(estimateVram('bf16', 4096, 768, 24 * 1024).fit).toBe('likely-too-small');
    expect(vramFitMessage(estimateVram('auto', 1280, 768))).toBe(
      'GPU memory capacity was not detected. Until it is known, Auto estimates the highest-memory BF16 profile.',
    );
  });
});
