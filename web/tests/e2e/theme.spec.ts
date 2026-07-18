import { expect, test } from '@playwright/test';

const themeStorageKey = 'see-through-theme';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/system', (route) =>
    route.fulfill({
      json: {
        gpu: { available: false },
        models: { bundles: {} },
        queue: { counts: {}, running_job_id: null },
        storage: {},
        version: 'test',
      },
    }),
  );
  await page.route('**/api/events', (route) => route.abort());
  await page.route('**/api/jobs', (route) => route.fulfill({ json: [] }));
});

test('offers an accessible theme toggle and persists an explicit light preference', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), themeStorageKey))
    .toBeNull();

  const toggle = page.getByRole('button', { name: 'Switch to light mode' });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');

  await toggle.click();

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  const darkToggle = page.getByRole('button', { name: 'Switch to dark mode' });
  await expect(darkToggle).toBeVisible();
  await expect(darkToggle).toHaveAttribute('aria-pressed', 'false');
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), themeStorageKey))
    .toBe('light');

  await page.reload();

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
});

test('uses the first-visit system light preference without storing an implicit choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), themeStorageKey))
    .toBeNull();
});

test('stored preference takes priority over the current system preference', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, value),
    { key: themeStorageKey, value: 'dark' },
  );
  await page.goto('/');

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', { name: 'Switch to light mode' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});
