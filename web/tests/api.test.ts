import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  createJob,
  deleteJob,
  getJobs,
  getSystem,
  normalizeJob,
  prefetchModels,
} from '../src/lib/api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('runtime API client', () => {
  it('accepts and normalizes the backend job-list response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: 'abc', state: 'queued', original_filename: 'input.png' }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await expect(getJobs()).resolves.toEqual([
      expect.objectContaining({
        id: 'abc',
        status: 'queued',
        state: 'queued',
        input_name: 'input.png',
      }),
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs',
      expect.objectContaining({ headers: { 'X-See-Through-Client': 'web' } }),
    );
  });

  it('normalizes persisted record and artifact aliases', () => {
    const job = normalizeJob({
      id: 'job-id',
      state: 'completed',
      original_filename: 'source.webp',
      profile_requested: 'group-offload',
      resolution: 1280,
      log_tail: ['complete'],
      artifacts: [
        {
          id: 'artifact-id',
          name: 'reconstruction.png',
          kind: 'reconstruction',
          size_bytes: 4096,
          content_type: 'image/png',
        },
      ],
    });
    expect(job).toEqual(
      expect.objectContaining({
        status: 'completed',
        input_name: 'source.webp',
        logs: ['complete'],
        config: expect.objectContaining({ profile: 'group-offload', resolution: 1280 }),
        reconstruction_url: '/api/jobs/job-id/artifacts/artifact-id',
      }),
    );
    expect(job.artifacts?.[0]).toEqual(
      expect.objectContaining({ size: 4096, mime_type: 'image/png' }),
    );
  });

  it('submits a multipart job with every inference control', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'created', status: 'queued' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['image'], 'source.png', { type: 'image/png' });
    await createJob(file, {
      profile: 'nf4',
      resolution: 2048,
      seed: 1234,
      steps: 42,
      depth_resolution: 1024,
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = init.body as FormData;
    expect(init.method).toBe('POST');
    expect(body.get('file')).toBeInstanceOf(File);
    expect(body.get('profile')).toBe('nf4');
    expect(body.get('resolution')).toBe('2048');
    expect(body.get('seed')).toBe('1234');
    expect(body.get('steps')).toBe('42');
    expect(body.get('depth_resolution')).toBe('1024');
  });

  it('uses the client header for JSON mutations and encoded job IDs', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await prefetchModels('bf16');
    await deleteJob('job/unsafe');
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-See-Through-Client': 'web',
        },
        body: JSON.stringify({ selection: 'bf16' }),
      }),
    );
    expect(fetchMock.mock.calls[1][0]).toBe('/api/jobs/job%2Funsafe');
  });

  it('retains model preparation state and errors from system status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            models: {
              bundles: {
                bf16: {
                  state: 'failed',
                  error: 'Insufficient model-cache space',
                  expected_size_bytes: 42,
                },
                nf4: { state: 'ready', error: null },
              },
            },
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );

    const status = await getSystem();
    expect(status.models?.bundles?.bf16).toEqual({
      state: 'failed',
      error: 'Insufficient model-cache space',
      expected_size_bytes: 42,
    });
    expect(status.models?.bundles?.nf4?.state).toBe('ready');
  });

  it('surfaces safe API error details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Model cache is full' }), {
          status: 507,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    await expect(getJobs()).rejects.toEqual(new ApiError('Model cache is full', 507));
  });
});
