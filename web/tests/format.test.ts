import { describe, expect, it } from 'vitest';
import { artifactUrl, formatBytes, jobProgress, logText, normalizeArtifacts } from '../src/lib/format';
import type { Job } from '../src/lib/types';

const baseJob: Job = { id: 'job-123', status: 'running' };

describe('frontend data formatting', () => {
  it('formats bounded progress from fractional and percentage values', () => {
    expect(jobProgress({ ...baseJob, progress: 0.42 })).toBe(42);
    expect(jobProgress({ ...baseJob, progress: 140 })).toBe(100);
    expect(jobProgress({ ...baseJob, progress: -1 })).toBe(0);
    expect(jobProgress({ ...baseJob, status: 'completed' })).toBe(100);
  });

  it('normalizes both supported artifact manifest shapes', () => {
    expect(
      normalizeArtifacts({
        ...baseJob,
        artifact_manifest: {
          'result.psd': { kind: 'psd', size: 2048 },
          'bundle.zip': '/download/bundle.zip',
        },
      }),
    ).toEqual([
      { name: 'result.psd', kind: 'psd', size: 2048 },
      { name: 'bundle.zip', url: '/download/bundle.zip' },
    ]);
  });

  it('uses encoded allowlisted artifact endpoints when the API omits a URL', () => {
    expect(artifactUrl('job / 1', { name: '../result file.psd' })).toBe(
      '/api/jobs/job%20%2F%201/artifacts/..%2Fresult%20file.psd',
    );
  });

  it('handles logs and byte sizes without unsafe assumptions', () => {
    expect(logText({ ...baseJob, logs: ['one', 'two'] })).toBe('one\ntwo');
    expect(logText(baseJob)).toContain('Waiting');
    expect(formatBytes(1024 ** 3)).toBe('1.00 GiB');
    expect(formatBytes()).toBe('—');
  });
});
