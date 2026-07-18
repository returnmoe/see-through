<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    cancelJob,
    connectEvents,
    createJob,
    deleteJob,
    getJobs,
    getSystem,
    prefetchModels,
  } from '@/lib/api';
  import {
    artifactUrl,
    formatBytes,
    formatMemory,
    jobLabel,
    jobProgress,
    logText,
    normalizeArtifacts,
  } from '@/lib/format';
  import type {
    Artifact,
    Job,
    MemoryProfile,
    ModelBundle,
    ModelState,
    Resolution,
    SystemStatus,
  } from '@/lib/types';
  import { estimateVram, formatVramRange, vramFitMessage } from '@/lib/vram';
  import '@/styles/app.css';

  const profiles: Array<{ value: MemoryProfile; label: string; detail: string }> = [
    { value: 'auto', label: 'Automatic', detail: 'Chooses a profile from the detected per-GPU VRAM.' },
    { value: 'bf16', label: 'Highest fidelity', detail: 'BF16 weights kept on the GPU · 18+ GiB recommended.' },
    { value: 'group-offload', label: 'Balanced', detail: 'BF16 weights with group offload · 11+ GiB recommended.' },
    { value: 'nf4', label: 'Low memory', detail: 'NF4 quantization with group offload · 8+ GiB.' },
    { value: 'blockswap', label: 'Maximum offload', detail: 'BF16 block swapping · slowest low-memory fallback.' },
  ];
  const resolutionPresets: Resolution[] = [768, 1024, 1280, 1536, 2048, 4096];
  const minResolution = 768;
  const maxResolution = 10240;
  const resolutionStep = 64;
  const minDepthResolution = 256;
  const maxDepthResolution = 2048;
  const activeStatuses = new Set(['queued', 'waiting_for_models', 'running']);
  const themeStorageKey = 'see-through-theme';
  type Theme = 'dark' | 'light';
  type Toast = {
    id: number;
    title: string;
    message: string;
    tone: 'danger';
  };
  type DialogName = 'profile' | 'credits';
  type ModelStateCopy = {
    label: string;
    detail: string;
  };

  let system: SystemStatus | null = null;
  let jobs: Job[] = [];
  let selectedId: string | null = null;
  let selectedJob: Job | undefined;
  let artifacts: Artifact[] = [];
  let imageArtifacts: Artifact[] = [];
  let file: File | null = null;
  let filePreview: string | null = null;
  let profile: MemoryProfile = 'auto';
  let resolution: Resolution = 1280;
  let seed = 42;
  let steps = 30;
  let depthResolution: Resolution = 768;
  let loading = true;
  let retryingApi = false;
  let submitting = false;
  let modelAction = false;
  let profilePickerOpen = false;
  let creditsOpen = false;
  let jobPickerOpen = false;
  let noticeMessage = '';
  let systemError: string | null = null;
  let jobsError: string | null = null;
  let toasts: Toast[] = [];
  let fileInput: HTMLInputElement;
  let profileTrigger: HTMLButtonElement;
  let creditsTrigger: HTMLButtonElement;
  let profileDialog: HTMLElement;
  let creditsDialog: HTMLElement;
  let dialogReturnFocus: HTMLElement | null = null;
  let refreshTimer: ReturnType<typeof setTimeout> | undefined;
  let toastSequence = 0;
  const toastTimers = new Map<number, ReturnType<typeof setTimeout>>();
  let modelBundleName = 'BF16 weights';
  let modelState: ModelState = 'unknown';
  let theme: Theme =
    typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
      ? 'light'
      : 'dark';

  $: selectedJob = jobs.find((job) => job.id === selectedId) ?? jobs[0];
  $: artifacts = normalizeArtifacts(selectedJob);
  $: imageArtifacts = artifacts.filter((artifact) =>
    artifact.kind === 'image' || artifact.mime_type?.startsWith('image/'),
  );
  $: vramEstimate = estimateVram(
    profile,
    resolution,
    depthResolution,
    system?.gpu?.memory_mib,
  );
  $: vramRange = formatVramRange(vramEstimate);
  $: vramCapacityMessage = vramFitMessage(vramEstimate, system?.gpu?.memory_mib);
  $: selectedBundle = (vramEstimate.profile === 'nf4' ? 'nf4' : 'bf16') as ModelBundle;
  $: selectedBundleEntry = system?.models?.bundles?.[selectedBundle];
  $: modelBundleName = selectedBundle === 'nf4' ? 'NF4 weights' : 'BF16 weights';
  $: modelState = selectedBundleEntry?.state ?? 'unknown';
  $: modelStatusCopy = describeModelState(modelState);
  $: modalOpen = profilePickerOpen || creditsOpen;
  $: gpuCount = system?.gpu?.count ?? system?.gpu?.devices?.length ?? (system?.gpu?.name ? 1 : 0);
  $: gpuLabel =
    gpuCount > 1
      ? `${gpuCount}× ${system?.gpu?.name ?? 'GPU'}`
      : system?.gpu?.name
        ? system.gpu.name
        : systemError
          ? 'Runtime unavailable'
          : loading
            ? 'Detecting GPU…'
            : system?.gpu?.available === false
              ? 'No GPU detected'
              : 'GPU unavailable';
  $: resolutionValid = isSteppedInteger(
    resolution,
    minResolution,
    maxResolution,
    resolutionStep,
  );
  $: seedValid = Number.isInteger(seed) && seed >= 0 && seed <= 4_294_967_295;
  $: stepsValid = Number.isInteger(steps) && steps >= 1 && steps <= 100;
  $: depthResolutionValid = isSteppedInteger(
    depthResolution,
    minDepthResolution,
    maxDepthResolution,
    resolutionStep,
  );
  $: settingsValid =
    resolutionValid &&
    seedValid &&
    stepsValid &&
    depthResolutionValid;

  async function refreshSystem(): Promise<void> {
    try {
      system = await getSystem();
      systemError = null;
    } catch (error) {
      systemError = errorMessage(error, 'Could not load GPU and model status.');
    }
  }

  async function refreshJobs(): Promise<void> {
    try {
      jobs = await getJobs();
      jobsError = null;
      if (!selectedId && jobs.length > 0) selectedId = jobs[0].id;
    } catch (error) {
      jobsError = errorMessage(error, 'Could not load the job queue.');
    }
  }

  async function refreshAll(): Promise<void> {
    try {
      await Promise.all([refreshSystem(), refreshJobs()]);
    } finally {
      loading = false;
    }
  }

  async function retryApiLoads(): Promise<void> {
    if (retryingApi) return;
    retryingApi = true;
    try {
      await refreshAll();
    } finally {
      retryingApi = false;
    }
  }

  function scheduleRefresh(): void {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => void refreshAll(), 120);
  }

  onMount(() => {
    const systemTheme = window.matchMedia('(prefers-color-scheme: light)');
    let followsSystemTheme = false;

    try {
      const storedTheme = window.localStorage.getItem(themeStorageKey);
      followsSystemTheme = storedTheme !== 'dark' && storedTheme !== 'light';
    } catch {
      followsSystemTheme = true;
    }

    applyTheme(
      document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
      false,
    );

    const handleSystemTheme = (event: MediaQueryListEvent): void => {
      if (followsSystemTheme) applyTheme(event.matches ? 'light' : 'dark', false);
    };
    systemTheme.addEventListener('change', handleSystemTheme);

    void refreshAll();
    const stopEvents = connectEvents(scheduleRefresh, (isConnected) => {
      if (isConnected) scheduleRefresh();
    });
    const poll = window.setInterval(() => void refreshSystem(), 15_000);
    return () => {
      stopEvents();
      window.clearInterval(poll);
      systemTheme.removeEventListener('change', handleSystemTheme);
      if (refreshTimer) clearTimeout(refreshTimer);
      toastTimers.forEach((timer) => clearTimeout(timer));
      if (filePreview) URL.revokeObjectURL(filePreview);
    };
  });

  function applyTheme(nextTheme: Theme, persist = true): void {
    theme = nextTheme;
    document.documentElement.dataset.theme = nextTheme;
    if (!persist) return;

    try {
      window.localStorage.setItem(themeStorageKey, nextTheme);
    } catch {
      // Theme switching still works when storage is unavailable.
    }
  }

  function toggleTheme(): void {
    applyTheme(theme === 'dark' ? 'light' : 'dark');
  }

  function chooseFile(nextFile: File | undefined): void {
    if (!nextFile) return;
    if (filePreview) URL.revokeObjectURL(filePreview);
    file = nextFile;
    filePreview = URL.createObjectURL(nextFile);
  }

  function onFileInput(event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    chooseFile(input.files?.[0]);
  }

  function onDrop(event: DragEvent): void {
    event.preventDefault();
    chooseFile(event.dataTransfer?.files[0]);
  }

  async function submit(): Promise<void> {
    if (!file || submitting || !settingsValid) return;
    submitting = true;
    noticeMessage = '';
    try {
      const created = await createJob(file, {
        profile,
        resolution,
        seed,
        steps,
        depth_resolution: depthResolution,
      });
      jobs = [created, ...jobs.filter((job) => job.id !== created.id)];
      selectedId = created.id;
      noticeMessage = `Run ${created.id.slice(0, 8)} entered the queue.`;
      file = null;
      if (filePreview) URL.revokeObjectURL(filePreview);
      filePreview = null;
      if (fileInput) fileInput.value = '';
    } catch (error) {
      notifyFailure(
        'Job submission failed',
        error instanceof Error ? error.message : 'Could not create the run.',
      );
    } finally {
      submitting = false;
    }
  }

  async function retryModelDownload(): Promise<void> {
    if (modelAction) return;
    modelAction = true;
    try {
      await prefetchModels(selectedBundle);
      noticeMessage = 'Model preparation requested.';
      await refreshSystem();
      scheduleRefresh();
    } catch (error) {
      notifyFailure(
        'Model preparation failed',
        error instanceof Error ? error.message : 'Could not prepare models.',
      );
    } finally {
      modelAction = false;
    }
  }

  async function cancelSelected(): Promise<void> {
    if (!selectedJob) return;
    try {
      const updated = await cancelJob(selectedJob.id);
      jobs = jobs.map((job) => (job.id === updated.id ? updated : job));
      noticeMessage = 'Cancellation requested.';
    } catch (error) {
      notifyFailure(
        'Cancellation failed',
        error instanceof Error ? error.message : 'Could not cancel this run.',
      );
    }
  }

  async function removeSelected(): Promise<void> {
    if (!selectedJob || activeStatuses.has(selectedJob.status)) return;
    const removedId = selectedJob.id;
    try {
      await deleteJob(removedId);
      jobs = jobs.filter((job) => job.id !== removedId);
      selectedId = jobs[0]?.id ?? null;
      noticeMessage = 'Run and its artifacts were removed.';
    } catch (error) {
      notifyFailure(
        'Job deletion failed',
        error instanceof Error ? error.message : 'Could not remove this run.',
      );
    }
  }

  function notifyFailure(title: string, message: string): void {
    const id = ++toastSequence;
    toasts = [...toasts, { id, title, message, tone: 'danger' }];
    noticeMessage = '';
    const timer = setTimeout(() => dismissToast(id), 5_000);
    toastTimers.set(id, timer);
  }

  function dismissToast(id: number): void {
    const timer = toastTimers.get(id);
    if (timer) clearTimeout(timer);
    toastTimers.delete(id);
    toasts = toasts.filter((toast) => toast.id !== id);
  }

  function chooseJob(jobId: string): void {
    selectedId = jobId;
    jobPickerOpen = false;
  }

  function statusLabel(status: string): string {
    return status.replaceAll('_', ' ');
  }

  function isSteppedInteger(value: number, minimum: number, maximum: number, step: number): boolean {
    return (
      Number.isInteger(value) &&
      value >= minimum &&
      value <= maximum &&
      value % step === 0
    );
  }

  function chooseResolution(size: Resolution): void {
    resolution = size;
  }

  function chooseProfile(nextProfile: MemoryProfile): void {
    profile = nextProfile;
    void closeDialog('profile');
  }

  function errorMessage(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
  }

  function describeModelState(state: ModelState): ModelStateCopy {
    switch (state) {
      case 'not_downloaded':
        return {
          label: 'Not prepared',
          detail: 'Download the pinned bundle now, or it will download when the first run starts.',
        };
      case 'downloading':
        return {
          label: 'Downloading',
          detail: 'Pinned weights are being cached. Queued runs will wait until preparation finishes.',
        };
      case 'ready':
        return {
          label: 'Ready',
          detail: 'The selected weights are cached and ready on this pod.',
        };
      case 'failed':
        return {
          label: 'Preparation failed',
          detail: 'The selected bundle could not be prepared. Review the error and retry.',
        };
      default:
        return {
          label: 'Status unavailable',
          detail: 'Model status will appear when the runtime responds.',
        };
    }
  }

  function dialogElement(name: DialogName): HTMLElement | undefined {
    return name === 'profile' ? profileDialog : creditsDialog;
  }

  async function openDialog(name: DialogName, trigger: HTMLElement): Promise<void> {
    dialogReturnFocus = trigger;
    jobPickerOpen = false;
    profilePickerOpen = name === 'profile';
    creditsOpen = name === 'credits';
    await tick();
    const dialog = dialogElement(name);
    const initialFocus =
      dialog?.querySelector<HTMLElement>('[data-initial-focus]') ?? dialog;
    initialFocus?.focus();
  }

  async function closeDialog(name: DialogName): Promise<void> {
    if (name === 'profile') profilePickerOpen = false;
    if (name === 'credits') creditsOpen = false;
    const returnFocus = dialogReturnFocus;
    dialogReturnFocus = null;
    await tick();
    returnFocus?.focus();
  }

  function handleDialogKeydown(event: KeyboardEvent, name: DialogName): void {
    if (event.key !== 'Tab') return;
    const dialog = dialogElement(name);
    if (!dialog) return;
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => !element.hasAttribute('hidden'));
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }
</script>

<svelte:head>
  <meta name="theme-color" content={theme === 'dark' ? '#17191a' : '#f4f5f6'} />
</svelte:head>

<svelte:window
  onkeydown={(event) => {
    if (event.key === 'Escape') {
      if (profilePickerOpen) {
        void closeDialog('profile');
      } else if (creditsOpen) {
        void closeDialog('credits');
      } else {
        jobPickerOpen = false;
      }
    }
  }}
  onclick={(event) => {
    if (
      jobPickerOpen &&
      (!(event.target instanceof Element) || !event.target.closest('.dock-run-picker'))
    ) {
      jobPickerOpen = false;
    }
  }}
/>

<div class="workspace-shell">
  <header class="system-bar" inert={modalOpen}>
    <a class="brand" href="/" aria-label="See-through workspace home">
      <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <span><small>See-Through</small><strong>Workspace</strong></span>
    </a>
    <span class="system-bar-separator" aria-hidden="true"></span>

    <section class="topbar-runtime" aria-label="Runtime configuration">
      <button
        bind:this={profileTrigger}
        class="profile-picker"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={profilePickerOpen}
        title={profiles.find((option) => option.value === profile)?.detail}
        onclick={() => void openDialog('profile', profileTrigger)}
      >
        <span class="profile-copy">
          <strong>{profiles.find((option) => option.value === profile)?.label}</strong>
          <small>Memory profile</small>
        </span>
        <svg class="profile-chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path d="m4 6 4 4 4-4"></path>
        </svg>
      </button>

      <div
        class="gpu-status"
        class:detecting={loading && !system?.gpu?.name && !systemError}
        class:unavailable={!loading && !system?.gpu?.name}
        title={gpuCount > 1 ? `${gpuCount} GPUs attached; one GPU is used per job` : 'Detected GPU'}
      >
        <strong>{gpuLabel}</strong>
        {#if system?.gpu?.name}
          <small>{formatMemory(system.gpu.memory_mib)}{gpuCount > 1 ? ' each' : ''}</small>
        {/if}
      </div>
    </section>

    <div class="header-actions">
      <button
        bind:this={creditsTrigger}
        class="header-action-link"
        type="button"
        aria-label="Credits and licenses"
        title="Credits and licenses"
        aria-haspopup="dialog"
        aria-expanded={creditsOpen}
        onclick={() => void openDialog('credits', creditsTrigger)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="9"></circle>
          <path d="M12 11v6M12 7.5v.5"></path>
        </svg>
      </button>
      <button
        class="theme-toggle"
        type="button"
        aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        aria-pressed={theme === 'dark'}
        title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        onclick={toggleTheme}
      >
        <span class="theme-icon" aria-hidden="true">
          {#if theme === 'dark'}
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"></path></svg>
          {:else}
            <svg viewBox="0 0 24 24"><path d="M20.4 15.1A8.5 8.5 0 0 1 8.9 3.6 8.5 8.5 0 1 0 20.4 15.1Z"></path></svg>
          {/if}
        </span>
      </button>
    </div>
  </header>

  {#if profilePickerOpen}
    <div
      class="dialog-backdrop"
      role="presentation"
      onclick={(event) => {
        if (event.target === event.currentTarget) void closeDialog('profile');
      }}
    >
      <section
        bind:this={profileDialog}
        class="profile-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-dialog-title"
        tabindex="-1"
        onkeydown={(event) => handleDialogKeydown(event, 'profile')}
      >
        <header>
          <div>
            <h2 id="profile-dialog-title">Choose memory profile</h2>
            <p>Choose how the model is held between GPU and system memory.</p>
          </div>
          <button
            class="dialog-close"
            type="button"
            aria-label="Close memory profile"
            data-initial-focus
            onclick={() => void closeDialog('profile')}
          >×</button>
        </header>
        <div class="profile-list">
          {#each profiles as option}
            <button
              type="button"
              class:active={option.value === profile}
              onclick={() => chooseProfile(option.value)}
            >
              <span>
                <strong>{option.label}</strong>
                <small>{option.detail}</small>
              </span>
              <span class="profile-list-meta">
                <em>{option.value === profile ? 'Active' : 'Select'}</em>
              </span>
            </button>
          {/each}
        </div>
        {#if modelState === 'failed' || modelState === 'not_downloaded'}
          <button
            class="prepare-button"
            type="button"
            onclick={retryModelDownload}
            disabled={modelAction}
          >
            {modelAction ? 'Requesting…' : `Prepare ${modelBundleName}`}
          </button>
        {/if}
      </section>
    </div>
  {/if}

  <div class="workspace-content" inert={profilePickerOpen}>
  {#if systemError || jobsError}
    <div class="message api-error" role="alert" inert={creditsOpen}>
      <span aria-hidden="true">!</span>
      <div>
        <strong>Runtime status could not be fully loaded</strong>
        {#if systemError}<p>System: {systemError}</p>{/if}
        {#if jobsError}<p>Jobs: {jobsError}</p>{/if}
      </div>
      <button
        class="message-retry"
        type="button"
        disabled={retryingApi}
        onclick={() => void retryApiLoads()}
      >{retryingApi ? 'Retrying…' : 'Retry'}</button>
    </div>
  {/if}
  {#if noticeMessage}
    <div class="message notice" role="status" inert={creditsOpen}><span>✓</span>{noticeMessage}<button type="button" aria-label="Dismiss notification" onclick={() => (noticeMessage = '')}>×</button></div>
  {/if}
  <main id="new-run" class="main-stage">
    <section class="run-grid" aria-label="New inference run" inert={creditsOpen}>
      <article class="panel upload-panel">
        <div class="panel-title"><span class="step-number">1</span><div><h2>Source image</h2><p>BMP, JPEG, PNG, WebP, or JXL · 50 MiB max</p></div></div>
        <label
          class="drop-zone"
          class:has-file={Boolean(file)}
          ondrop={onDrop}
          ondragover={(event) => event.preventDefault()}
        >
          <input bind:this={fileInput} type="file" accept=".bmp,.jpg,.jpeg,.png,.webp,.jxl,image/*" onchange={onFileInput} />
          {#if filePreview && file}
            <img src={filePreview} alt="Selected source preview" />
            <span class="file-overlay"><strong>{file.name}</strong><small>{formatBytes(file.size)} · Click to replace</small></span>
          {:else}
            <span class="upload-glyph" aria-hidden="true">↥</span>
            <strong>Drop an image here</strong>
            <span>or <u>browse local files</u></span>
            <small>Decoded dimensions are limited to 10240 × 10240</small>
          {/if}
        </label>
      </article>

      <article class="panel configure-panel">
        <div class="panel-title"><span class="step-number">2</span><div><h2>Output settings</h2><p>Working sizes and inference recipe</p></div></div>
        <fieldset>
          <legend>LayerDiff working resolution</legend>
          <div class="resolution-switch">
            {#each resolutionPresets as size}
              <button
                type="button"
                class:checked={resolution === size}
                aria-pressed={resolution === size}
                onclick={() => chooseResolution(size)}
              ><strong>{size}</strong><small>px</small></button>
            {/each}
          </div>
          <label class="number-control resolution-input">
            <span>Custom square size</span>
            <span class="input-with-suffix">
              <input
                name="resolution"
                type="number"
                min={minResolution}
                max={maxResolution}
                step={resolutionStep}
                bind:value={resolution}
                aria-invalid={!resolutionValid}
              />
              <small>px</small>
            </span>
          </label>
          {#if !resolutionValid}
            <p class="validation-message" role="alert">Use a multiple of 64 from 768 through 10240.</p>
          {/if}
          <p class="setting-help">
            1280 px is the released V3 baseline. Higher sizes are untiled and experimental; a
            10000 px source can use 9984 or 10048.
          </p>
        </fieldset>

        <fieldset class="recipe-settings">
          <legend>Inference recipe</legend>
          <div class="recipe-grid">
            <label class="number-control">
              <span>Seed</span>
              <input name="seed" type="number" min="0" max="4294967295" step="1" bind:value={seed} aria-invalid={!seedValid} />
              <small>0–4294967295</small>
            </label>
            <label class="number-control">
              <span>LayerDiff steps</span>
              <input name="steps" type="number" min="1" max="100" step="1" bind:value={steps} aria-invalid={!stepsValid} />
              <small>1–100 · default 30</small>
            </label>
            <label class="number-control">
              <span>Marigold size</span>
              <span class="input-with-suffix">
                <input
                  name="depth_resolution"
                  type="number"
                  min={minDepthResolution}
                  max={maxDepthResolution}
                  step={resolutionStep}
                  bind:value={depthResolution}
                  aria-invalid={!depthResolutionValid}
                />
                <small>px</small>
              </span>
              <small>Default · 768</small>
            </label>
          </div>
        </fieldset>

        <section class="vram-estimate {vramEstimate.fit}" aria-live="polite">
          <div class="estimate-heading">
            <span>Estimated peak VRAM <small>on one GPU</small></span>
            <strong>{vramRange}</strong>
          </div>
          <p>
            {#if vramEstimate.extrapolated}
              Rough extrapolation from the published 1280 / 768 guidance using square-pixel area. The actual peak may be substantially higher.
            {:else}
              Planning range derived from the project’s published 1280 guidance; image, allocator, driver, and libraries can change the peak.
            {/if}
          </p>
          <div class="capacity-message"><i aria-hidden="true"></i><span>{vramCapacityMessage}</span></div>
          {#if gpuCount > 1}
            <p class="multi-gpu-note">{gpuCount} GPUs are attached, but this runtime uses one GPU per job; their memory is not pooled.</p>
          {/if}
        </section>

        <section
          class="model-preparation {modelState}"
          aria-label="Model preparation"
          aria-live="polite"
        >
          <span class="model-state-mark" aria-hidden="true"></span>
          <div class="model-state-copy">
            <div>
              <span>{modelBundleName}</span>
              <strong>{modelStatusCopy.label}</strong>
            </div>
            <p>{modelStatusCopy.detail}</p>
            {#if modelState === 'failed' && selectedBundleEntry?.error}
              <p class="model-state-error" role="alert">{selectedBundleEntry.error}</p>
            {/if}
          </div>
          {#if modelState === 'failed' || modelState === 'not_downloaded'}
            <button
              class="model-action"
              type="button"
              onclick={retryModelDownload}
              disabled={modelAction}
            >
              {modelAction
                ? 'Requesting…'
                : modelState === 'failed'
                  ? 'Retry preparation'
                  : 'Prepare now'}
            </button>
          {/if}
        </section>

        <button
          class="primary-button"
          type="button"
          disabled={!file || submitting || loading || Boolean(systemError) || !settingsValid}
          onclick={submit}
        >
          <span>{submitting ? 'Submitting…' : 'Start decomposition'}</span><b aria-hidden="true">→</b>
        </button>
        <small class="launch-note">The selected model bundle downloads separately and is never embedded in the image.</small>
      </article>
    </section>

    {#if creditsOpen}
      <div
        class="dialog-backdrop"
        role="presentation"
        onclick={(event) => {
          if (event.target === event.currentTarget) void closeDialog('credits');
        }}
      >
        <section
          bind:this={creditsDialog}
          id="credits"
          class="credits-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="credits-title"
          tabindex="-1"
          onkeydown={(event) => handleDialogKeydown(event, 'credits')}
        >
          <header class="credits-heading">
            <div>
              <h2 id="credits-title">Credits and licenses</h2>
              <p>This interface brings together independently published research, source code, and model weights.</p>
            </div>
            <button
              class="dialog-close"
              type="button"
              aria-label="Close credits and licenses"
              data-initial-focus
              onclick={() => void closeDialog('credits')}
            >×</button>
          </header>

          <div class="credits-grid">
        <article>
          <h3>Research and paper</h3>
          <p>
            <a href="https://arxiv.org/abs/2602.03749" target="_blank" rel="noreferrer"><cite>See-through: Single-image Layer Decomposition for Anime Characters</cite></a>
            is by Jian Lin, Chengze Li, Haoyun Qin, Kwun Wang Chan, Yanghua Jin, Hanyuan Liu,
            Stephen Chun Wang Choy, and Xueting Liu.
          </p>
          <p class="credit-detail">
            Jian Lin, Chengze Li, Kwun Wang Chan, Hanyuan Liu, Stephen Chun Wang Choy, and
            Xueting Liu — Saint Francis University; Haoyun Qin — University of Pennsylvania,
            Spellbrush, and Shitagaki Lab; Yanghua Jin — Spellbrush. Chengze Li is the
            corresponding author.
          </p>
          <a class="credit-license" href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noreferrer">Manuscript · CC BY-NC-SA 4.0</a>
        </article>

        <article>
          <h3>Upstream source code</h3>
          <p>
            The <a href="https://github.com/shitagaki-lab/see-through" target="_blank" rel="noreferrer">official See-Through repository</a>
            is published by Shitagaki Lab and contributors under the
            <a href="https://github.com/shitagaki-lab/see-through/blob/main/LICENSE" target="_blank" rel="noreferrer">Apache License 2.0</a>.
          </p>
          <p class="credit-detail">The repository’s license does not name a separate copyright holder, so none is inferred here.</p>
        </article>

        <article class="models-credit">
          <h3>Model weights used by this workflow</h3>
          <p>
            The weights are published on Hugging Face by <code>layerdifforg</code> and
            <code>24yearsold</code>. LayerDiff 3D carries Apache-2.0 metadata; the other
            listed weights are Apache 2.0
            <a href="https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d/discussions/1" target="_blank" rel="noreferrer">per the publisher’s licensing statement</a>.
          </p>
          <ul class="model-links">
            <li><a href="https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d" target="_blank" rel="noreferrer"><strong>LayerDiff 3D</strong><span>layerdifforg · BF16</span></a></li>
            <li><a href="https://huggingface.co/layerdifforg/seethroughv0.0.1_marigold" target="_blank" rel="noreferrer"><strong>Marigold Depth</strong><span>layerdifforg · BF16</span></a></li>
            <li><a href="https://huggingface.co/24yearsold/seethroughv0.0.2_layerdiff3d_nf4" target="_blank" rel="noreferrer"><strong>LayerDiff 3D NF4</strong><span>24yearsold · quantized</span></a></li>
            <li><a href="https://huggingface.co/24yearsold/seethroughv0.0.1_marigold_nf4" target="_blank" rel="noreferrer"><strong>Marigold Depth NF4</strong><span>24yearsold · quantized</span></a></li>
          </ul>
        </article>

        <article class="ui-credit">
          <h3>This interface</h3>
          <p>
            This general-purpose convenience interface and its optional deployment packaging are
            independent work by return moe. They are not approved by, endorsed by, sponsored by, or otherwise affiliated with
            the See-Through authors, Shitagaki Lab, their institutions, or the model publishers.
          </p>
        </article>
          </div>
        </section>
      </div>
    {/if}
  </main>

  <aside class="artifact-dock" aria-label="Selected run details" inert={creditsOpen}>
    <div class="panel-title dock-head">
      <div>
        <h2>Jobs</h2>
        <p>Queue, progress, and artifacts</p>
      </div>
      {#if selectedJob}<span class="status-badge {selectedJob.status}">{statusLabel(selectedJob.status)}</span>{/if}
    </div>
    <div class="dock-content">
      {#if jobs.length > 0 && selectedJob}
        <div class="dock-run-picker">
          <span class="rail-label">Selected job</span>
          <button
            class="job-picker-trigger"
            type="button"
            aria-label="Selected job"
            aria-haspopup="listbox"
            aria-expanded={jobPickerOpen}
            onclick={() => (jobPickerOpen = !jobPickerOpen)}
          >
            <span class="job-status-mark {selectedJob.status}" aria-hidden="true"></span>
            <span class="job-picker-copy">
              <strong>{jobLabel(selectedJob)}</strong>
              <small>{statusLabel(selectedJob.status)}</small>
            </span>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"></path></svg>
          </button>
          {#if jobPickerOpen}
            <div class="job-picker-menu" role="listbox" aria-label="Jobs">
              {#each jobs as job}
                <button
                  type="button"
                  role="option"
                  aria-selected={job.id === selectedJob.id}
                  class:active={job.id === selectedJob.id}
                  onclick={() => chooseJob(job.id)}
                >
                  <span class="job-status-mark {job.status}" aria-hidden="true"></span>
                  <span class="job-picker-copy">
                    <strong>{jobLabel(job)}</strong>
                    <small>{statusLabel(job.status)}</small>
                  </span>
                  {#if job.id === selectedJob.id}<span class="job-picker-check" aria-hidden="true">✓</span>{/if}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    {#if selectedJob}
      {#if selectedJob.error}
        <div class="dock-error" role="alert">
          <header>
            <strong>Run failed</strong>
            <button
              type="button"
              aria-label="Dismiss failed job"
              title="Dismiss failed job"
              onclick={removeSelected}
            >×</button>
          </header>
          <p>{selectedJob.error}</p>
        </div>
      {/if}
      <section class="preview-card" aria-labelledby="preview-title">
        <div class="dock-section-title"><h2 id="preview-title">Preview</h2><span>{Math.round(jobProgress(selectedJob))}%</span></div>
        <div class="preview-frame">
          {#if selectedJob.reconstruction_url || selectedJob.preview_url}
            <img src={selectedJob.reconstruction_url ?? selectedJob.preview_url} alt="Reconstructed result" />
          {:else if imageArtifacts[0]}
            <img src={artifactUrl(selectedJob.id, imageArtifacts[0])} alt={imageArtifacts[0].label ?? 'Generated result'} />
          {:else}
            <div class="preview-placeholder"><span aria-hidden="true">◫</span><p>{activeStatuses.has(selectedJob.status) ? 'Preview appears after reconstruction' : 'No preview available'}</p></div>
          {/if}
        </div>
      </section>

      {#if imageArtifacts.length > 1}
        <section class="layers" aria-labelledby="layers-title"><div class="dock-section-title"><h2 id="layers-title">Layers</h2><span>{imageArtifacts.length}</span></div><div class="layer-grid">{#each imageArtifacts.slice(0, 6) as artifact}<a href={artifactUrl(selectedJob.id, artifact)} target="_blank" rel="noreferrer"><img src={artifactUrl(selectedJob.id, artifact)} alt={artifact.label ?? artifact.name} /><small>{artifact.label ?? artifact.name}</small></a>{/each}</div></section>
      {/if}

      <section class="artifacts" aria-labelledby="artifacts-title">
        <div class="dock-section-title"><h2 id="artifacts-title">Artifacts</h2><span>{artifacts.length}</span></div>
        {#if artifacts.length > 0}
          <div class="artifact-list">{#each artifacts as artifact}<a href={artifactUrl(selectedJob.id, artifact)} download><span class="artifact-type">{artifact.name.split('.').pop()?.toUpperCase() ?? 'FILE'}</span><span><strong>{artifact.label ?? artifact.name}</strong><small>{formatBytes(artifact.size)}</small></span><b aria-hidden="true">↓</b></a>{/each}</div>
        {:else}
          <p class="muted-copy">Downloads appear when processing completes.</p>
        {/if}
      </section>

      <section class="logs" aria-labelledby="logs-title"><div class="dock-section-title"><h2 id="logs-title">Live output</h2><span class:blink={activeStatuses.has(selectedJob.status)}>●</span></div><pre>{logText(selectedJob)}</pre></section>
      <div class="dock-actions">
        {#if activeStatuses.has(selectedJob.status)}
          <button class="danger-button" type="button" onclick={cancelSelected}>Cancel run</button>
        {:else}
          <button class="danger-button" type="button" onclick={removeSelected}>Delete run</button>
        {/if}
      </div>
    {:else}
      <div class="dock-empty"><span aria-hidden="true">▧</span></div>
    {/if}
    </div>
  </aside>
  </div>

  <div class="toast-region" aria-live="assertive" aria-relevant="additions" inert={modalOpen}>
    {#each toasts as toast (toast.id)}
      <div class="toast" data-tone={toast.tone} role="alert">
        <span class="toast-tone" aria-hidden="true"></span>
        <div>
          <strong>{toast.title}</strong>
          <p>{toast.message}</p>
          <small>Just now</small>
        </div>
        <button
          type="button"
          aria-label={`Dismiss notification: ${toast.title}`}
          onclick={() => dismissToast(toast.id)}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"></path></svg>
        </button>
      </div>
    {/each}
  </div>

</div>
