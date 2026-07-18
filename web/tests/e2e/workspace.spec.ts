import { expect, test } from '@playwright/test';

const system = {
  gpu: { available: true, name: 'NVIDIA RTX 4090', memory_mib: 24564, driver_version: '570.00' },
  models: { bundles: { bf16: { state: 'ready' }, nf4: { state: 'not_downloaded' } } },
  queue: { counts: {}, running_job_id: null },
  storage: { disk_free_bytes: 80 * 1024 ** 3 },
  version: 'test',
};

let createFailure: { status: number; detail: string } | null;

test.beforeEach(async ({ page }) => {
  createFailure = null;
  await page.route('**/api/system', (route) => route.fulfill({ json: system }));
  await page.route('**/api/events', (route) => route.abort());
  await page.route('**/api/jobs', async (route) => {
    if (route.request().method() === 'POST') {
      if (createFailure) {
        await route.fulfill({
          status: createFailure.status,
          json: { detail: createFailure.detail },
        });
        return;
      }
      await route.fulfill({
        json: {
          id: 'test-job-123',
          status: 'queued',
          input_name: 'sample.png',
          config: { profile: 'auto', resolution: 1280 },
        },
      });
      return;
    }
    await route.fulfill({ json: [] });
  });
});

test('renders the private single-image workflow', async ({ page }) => {
  await page.goto('/');
  const workflow = page.getByRole('region', { name: 'New inference run' });
  await expect(workflow.getByRole('heading', { name: 'Source image' })).toBeVisible();
  await expect(workflow.getByRole('button', { name: 'Start decomposition' })).toBeDisabled();
  await expect(page.locator('header').getByText('NVIDIA RTX 4090')).toBeVisible();
  await expect(page.locator('header .connection-state')).toHaveCount(0);
  await expect(page.locator('header .signal-dot')).toHaveCount(0);
  await expect(page.locator('.vram-estimate')).toContainText('Estimated peak VRAM');
  await expect(workflow.getByRole('heading', { name: 'Process' })).toHaveCount(0);
  await expect(page.getByText('No selection', { exact: true })).toHaveCount(0);
});

test('keeps all three panels aligned and uses internal Jobs scrolling on short monitors', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 480 });
  await page.route('**/api/jobs', (route) =>
    route.fulfill({
      json: [
        {
          id: 'completed-job',
          status: 'completed',
          input_name: 'layered.png',
          progress: 1,
          logs: Array.from({ length: 30 }, (_, index) => `Completed stage ${index + 1}`),
          artifacts: Array.from({ length: 10 }, (_, index) => ({
            id: `artifact-${index}`,
            name: `layer-${index}.json`,
            label: `Layer metadata ${index}`,
            kind: 'metadata',
            size: 1024 + index,
          })),
        },
        {
          id: 'failed-job',
          status: 'failed',
          input_name: 'failed-layer.png',
          error: 'Worker disconnected.',
        },
      ],
    }),
  );
  await page.route('**/api/jobs/**', (route) => route.fulfill({ status: 204 }));
  await page.goto('/');

  const upload = page.locator('.upload-panel');
  const configure = page.locator('.configure-panel');
  const jobs = page.locator('.artifact-dock');
  const panelHeights = await Promise.all(
    [upload, configure, jobs].map((panel) =>
      panel.evaluate((element) => element.getBoundingClientRect().height),
    ),
  );
  expect(Math.max(...panelHeights) - Math.min(...panelHeights)).toBeLessThan(1);

  const sourceTitle = await upload.getByRole('heading', { name: 'Source image' }).evaluate(
    (element) => ({
      fontSize: getComputedStyle(element).fontSize,
      fontWeight: getComputedStyle(element).fontWeight,
    }),
  );
  const jobsTitle = await jobs.getByRole('heading', { name: 'Jobs' }).evaluate((element) => ({
    fontSize: getComputedStyle(element).fontSize,
    fontWeight: getComputedStyle(element).fontWeight,
  }));
  expect(jobsTitle).toEqual(sourceTitle);

  const sourceHeader = await upload.locator('.panel-title').boundingBox();
  const jobsHeader = await jobs.locator('.panel-title').boundingBox();
  expect(jobsHeader?.height).toBe(sourceHeader?.height);
  await expect(jobs.locator('.panel-title p')).toHaveText('Queue, progress, and artifacts');

  const recipeBottom = await configure
    .locator('.recipe-settings')
    .evaluate((element) => element.getBoundingClientRect().bottom);
  const estimateTop = await configure
    .locator('.vram-estimate')
    .evaluate((element) => element.getBoundingClientRect().top);
  expect(estimateTop - recipeBottom).toBeGreaterThanOrEqual(10);

  const sharedScroller = await page.locator('.workspace-content').evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(sharedScroller.scrollHeight).toBeGreaterThan(sharedScroller.clientHeight);

  const jobsScroller = jobs.locator('.dock-content');
  const jobsOverflow = await jobsScroller.evaluate((element) => ({
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
    scrollHeight: element.scrollHeight,
  }));
  expect(jobsOverflow.overflowY).toBe('auto');
  expect(jobsOverflow.scrollHeight).toBeGreaterThan(jobsOverflow.clientHeight);

  await jobs.getByRole('button', { name: 'Selected job' }).click();
  const jobOptions = jobs.getByRole('option');
  await expect(jobOptions).toHaveCount(2);
  const optionHeights = await jobOptions.evaluateAll((options) =>
    options.map((option) => option.getBoundingClientRect().height),
  );
  expect(Math.min(...optionHeights)).toBeGreaterThanOrEqual(54);
  const failedOption = jobOptions.filter({ hasText: 'failed-layer.png' });
  await expect(failedOption).toContainText('failed');
  await failedOption.click();
  await expect(jobs.getByRole('alert')).toContainText('Run failed');
  await expect(jobs.getByRole('alert')).toContainText('Worker disconnected.');
  await jobs.getByRole('button', { name: 'Dismiss failed job' }).click();
  await expect(jobs.getByRole('alert')).toHaveCount(0);
  await expect(jobs.getByRole('button', { name: 'Selected job' })).toContainText(
    'layered.png',
  );

  await jobsScroller.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect.poll(() => jobsScroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  await page.locator('.resolution-switch button').filter({ hasText: '2048' }).click();
  await expect(configure.locator('.vram-estimate')).toContainText('Rough extrapolation');
  await expect(configure.locator('.vram-estimate')).not.toContainText(
    'High-uncertainty extrapolation',
  );

  await page.setViewportSize({ width: 1100, height: 480 });
  const stackedBounds = await Promise.all(
    [upload, configure, jobs].map((panel) =>
      panel.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return { bottom: bounds.bottom, top: bounds.top };
      }),
    ),
  );
  expect(Math.abs(stackedBounds[2].top - stackedBounds[0].top)).toBeLessThan(1);
  expect(Math.abs(stackedBounds[2].bottom - stackedBounds[1].bottom)).toBeLessThan(1);
});

test('submits an image with the selected controls', async ({ page }) => {
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'sample.png',
    mimeType: 'image/png',
    buffer: Buffer.from('not-decoded-by-the-static-client'),
  });
  await page.locator('.profile-picker').click();
  await page.getByRole('dialog', { name: 'Choose memory profile' }).getByRole('button', { name: /^Low memory/ }).click();
  await page.locator('.resolution-switch button').filter({ hasText: '1024' }).click();
  await page.locator('input[name="seed"]').fill('123456');
  await page.locator('input[name="steps"]').fill('40');
  await page.locator('input[name="depth_resolution"]').fill('1024');
  await page.getByRole('button', { name: 'Start decomposition' }).click();
  await expect(page.getByText('entered the queue')).toBeVisible();
  const selectedJob = page.getByRole('button', { name: 'Selected job' });
  await expect(selectedJob).toContainText('sample.png');
  await expect(selectedJob).toContainText('queued');

  const panelHeights = await Promise.all(
    [page.locator('.configure-panel'), page.locator('.artifact-dock')].map((panel) =>
      panel.evaluate((element) => element.getBoundingClientRect().height),
    ),
  );
  expect(Math.abs(panelHeights[0] - panelHeights[1])).toBeLessThan(1);
});

test('shows a failed submission as a dismissible top-right toast, not a job', async ({ page }) => {
  createFailure = { status: 503, detail: 'RunPod backend unavailable' };
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'failed-sample.png',
    mimeType: 'image/png',
    buffer: Buffer.from('not-decoded-by-the-static-client'),
  });

  await page.getByRole('button', { name: 'Start decomposition' }).click();

  const dock = page.getByRole('complementary', { name: 'Selected run details' });
  await expect(dock.getByRole('button', { name: 'Selected job' })).toHaveCount(0);
  await expect(dock.getByRole('alert')).toHaveCount(0);
  await expect(dock.getByText('Jobs', { exact: true })).toBeVisible();

  const toast = page.locator('.toast');
  await expect(toast).toHaveCount(1);
  await expect(toast).toContainText('Job submission failed');
  await expect(toast).toContainText('RunPod backend unavailable');
  await expect(toast).toHaveAttribute('data-tone', 'danger');
  expect(await toast.evaluate((element) => getComputedStyle(element).animationName)).toBe(
    'toast-attention',
  );
  const toastBounds = await toast.boundingBox();
  expect(toastBounds?.y).toBeGreaterThan(72);
  expect(
    (page.viewportSize()?.width ?? 0) -
      ((toastBounds?.x ?? 0) + (toastBounds?.width ?? 0)),
  ).toBeLessThanOrEqual(16);

  await page
    .getByRole('button', { name: 'Dismiss notification: Job submission failed' })
    .click();
  await expect(toast).toHaveCount(0);
});

test('uses the full upload panel and a Miru-proportioned left-aligned top bar', async ({
  page,
}) => {
  await page.goto('/');

  const header = page.locator('header');
  const headerBox = await header.boundingBox();
  const brandBox = await header.locator('.brand').boundingBox();
  const runtimeBox = await header.locator('.topbar-runtime').boundingBox();
  const panelBox = await page.locator('.upload-panel').boundingBox();
  const dropBox = await page.locator('.drop-zone').boundingBox();

  expect(headerBox?.height).toBe(72);
  expect(runtimeBox?.x).toBeGreaterThan(brandBox?.x ?? 0);
  expect(runtimeBox?.x).toBeLessThan(420);
  expect((panelBox?.y ?? 0) + (panelBox?.height ?? 0) - ((dropBox?.y ?? 0) + (dropBox?.height ?? 0))).toBeLessThanOrEqual(21);

  await header.locator('.profile-picker').click({ position: { x: 4, y: 4 } });
  const profileDialog = page.getByRole('dialog', { name: 'Choose memory profile' });
  await expect(profileDialog).toBeVisible();
  await expect(profileDialog).not.toContainText('Model unknown');
});

test('keeps the 320px top bar contained without overlapping runtime actions', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('/');

  const header = page.locator('.system-bar');
  const runtime = header.locator('.topbar-runtime');
  const actions = header.locator('.header-actions');
  const [runtimeBox, actionsBox] = await Promise.all([
    runtime.boundingBox(),
    actions.boundingBox(),
  ]);

  expect(runtimeBox).not.toBeNull();
  expect(actionsBox).not.toBeNull();
  expect((runtimeBox?.x ?? 0) + (runtimeBox?.width ?? 0)).toBeLessThanOrEqual(
    actionsBox?.x ?? 0,
  );
  expect((actionsBox?.x ?? 0) + (actionsBox?.width ?? 0)).toBeLessThanOrEqual(310);
  await expect(header.locator('.profile-picker')).toBeVisible();
  expect(
    await header.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    })),
  ).toEqual({ clientWidth: 320, scrollWidth: 320 });
});

test('shows model preparation state and supports prepare and retry actions', async ({
  page,
}) => {
  let state: 'not_downloaded' | 'downloading' | 'ready' | 'failed' = 'not_downloaded';
  let modelError: string | null = null;
  let requestedBundle = '';
  await page.unroute('**/api/system');
  await page.route('**/api/system', (route) =>
    route.fulfill({
      json: {
        ...system,
        models: {
          bundles: {
            bf16: { state, error: modelError },
            nf4: { state: 'not_downloaded' },
          },
        },
      },
    }),
  );
  await page.route('**/api/models/prefetch', async (route) => {
    requestedBundle = (await route.request().postDataJSON()).selection;
    await route.fulfill({ status: 202, json: { selection: requestedBundle, status: 'scheduled' } });
  });
  await page.goto('/');

  const preparation = page.getByRole('region', { name: 'Model preparation' });
  await expect(preparation).toContainText('BF16 weights');
  await expect(preparation).toContainText('Not prepared');
  await preparation.getByRole('button', { name: 'Prepare now' }).click();
  await expect.poll(() => requestedBundle).toBe('bf16');

  state = 'downloading';
  await page.reload();
  await expect(preparation).toContainText('Downloading');
  await expect(preparation).toContainText('Queued runs will wait');
  await expect(preparation.getByRole('button')).toHaveCount(0);

  state = 'ready';
  await page.reload();
  await expect(preparation).toContainText('Ready');
  await expect(preparation).toContainText('cached and ready');

  state = 'failed';
  modelError = 'Insufficient model-cache space';
  await page.reload();
  await expect(preparation).toContainText('Preparation failed');
  await expect(preparation.getByRole('alert')).toHaveText('Insufficient model-cache space');
  await expect(preparation.getByRole('button', { name: 'Retry preparation' })).toBeVisible();
});

test('surfaces initial API failures, stops detecting indefinitely, and retries in place', async ({
  page,
}) => {
  let healthy = false;
  await page.unroute('**/api/system');
  await page.unroute('**/api/jobs');
  await page.route('**/api/system', (route) =>
    healthy
      ? route.fulfill({ json: system })
      : route.fulfill({ status: 503, json: { detail: 'GPU runtime is starting' } }),
  );
  await page.route('**/api/jobs', (route) =>
    healthy
      ? route.fulfill({ json: [] })
      : route.fulfill({ status: 502, json: { detail: 'Queue unavailable' } }),
  );
  await page.goto('/');

  const failure = page.locator('.api-error');
  await expect(failure).toBeVisible();
  await expect(failure).toContainText('System: GPU runtime is starting');
  await expect(failure).toContainText('Jobs: Queue unavailable');
  await expect(page.locator('.gpu-status')).toContainText('Runtime unavailable');
  await expect(page.getByText('Detecting GPU…')).toHaveCount(0);

  healthy = true;
  await failure.getByRole('button', { name: 'Retry' }).click();
  await expect(failure).toHaveCount(0);
  await expect(page.locator('.gpu-status')).toContainText('NVIDIA RTX 4090');
});

test('keeps all primary controls keyboard reachable', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'See-through workspace home' })).toBeFocused();
  await expect(page.locator('main')).toHaveAttribute('id', 'new-run');
  await expect(page.getByRole('button', { name: 'Credits and licenses' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Credits and licenses' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Queue and recent runs' })).toHaveCount(0);
});

test('traps modal focus, makes the background inert, closes on Escape, and restores focus', async ({
  page,
}) => {
  await page.goto('/');

  const profileTrigger = page.locator('.profile-picker');
  await profileTrigger.click();
  const profileDialog = page.getByRole('dialog', { name: 'Choose memory profile' });
  const profileClose = profileDialog.getByRole('button', { name: 'Close memory profile' });
  await expect(profileClose).toBeFocused();
  await expect(page.locator('.workspace-content')).toHaveAttribute('inert', '');
  await page.keyboard.press('Shift+Tab');
  await expect(profileDialog.getByRole('button', { name: /^Maximum offload/ })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(profileClose).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(profileDialog).toHaveCount(0);
  await expect(profileTrigger).toBeFocused();

  const creditsTrigger = page.getByRole('button', { name: 'Credits and licenses' });
  await creditsTrigger.click();
  const credits = page.getByRole('dialog', { name: 'Credits and licenses' });
  const creditsClose = credits.getByRole('button', { name: 'Close credits and licenses' });
  await expect(creditsClose).toBeFocused();
  await expect(page.locator('.system-bar')).toHaveAttribute('inert', '');
  await expect(page.locator('.run-grid')).toHaveAttribute('inert', '');
  await expect(page.locator('.artifact-dock')).toHaveAttribute('inert', '');
  await page.keyboard.press('Shift+Tab');
  await expect(
    credits.getByRole('link', { name: /Marigold Depth NF4/ }),
  ).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(creditsClose).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(credits).toHaveCount(0);
  await expect(creditsTrigger).toBeFocused();
});

test('opens the permanent credits from the header and shows complete attribution', async ({
  page,
}) => {
  await page.goto('/');

  const header = page.locator('header');
  await header.getByRole('button', { name: /credits/i }).click();

  const credits = page.getByRole('dialog', { name: 'Credits and licenses' });
  await expect(credits).toBeVisible();
  await expect(credits).toBeInViewport();
  await expect(credits.getByRole('heading', { name: 'Research and paper' })).toBeVisible();
  await expect(
    credits.getByRole('link', {
      name: 'See-through: Single-image Layer Decomposition for Anime Characters',
    }),
  ).toBeVisible();
  await expect(credits.getByRole('link', { name: 'Manuscript · CC BY-NC-SA 4.0' })).toBeVisible();
  await expect(credits.getByRole('link', { name: 'Apache License 2.0' })).toBeVisible();

  const models = credits.locator('.models-credit');
  await expect(models).toContainText('layerdifforg');
  await expect(models).toContainText('24yearsold');
  for (const model of [
    {
      name: 'LayerDiff 3D',
      url: 'https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d',
    },
    {
      name: 'Marigold Depth',
      url: 'https://huggingface.co/layerdifforg/seethroughv0.0.1_marigold',
    },
    {
      name: 'LayerDiff 3D NF4',
      url: 'https://huggingface.co/24yearsold/seethroughv0.0.2_layerdiff3d_nf4',
    },
    {
      name: 'Marigold Depth NF4',
      url: 'https://huggingface.co/24yearsold/seethroughv0.0.1_marigold_nf4',
    },
  ]) {
    const modelLink = models.locator(`a[href="${model.url}"]`);
    await expect(modelLink).toHaveCount(1);
    await expect(modelLink).toContainText(model.name);
    await expect(modelLink).toBeVisible();
  }

  await expect(credits.locator('.ui-credit')).toContainText(
    'not approved by, endorsed by, sponsored by, or otherwise affiliated with the See-Through authors, Shitagaki Lab, their institutions, or the model publishers',
  );
});
