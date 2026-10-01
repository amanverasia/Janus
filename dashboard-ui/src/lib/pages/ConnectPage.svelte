<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { dashboardFetch, getState, mutate, responseError } from '$lib/api';
  import type { ValidatedMutationOptions } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { firstList, list, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: ValidatedMutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;

  type Source = { id: string; label: string; text: string };
  type AttachedFile = Source & { size: number };
  type PreviewRow = {
    source: string;
    key_masked: string;
    label: string;
    provider: string;
    format: string;
    status: 'new' | 'exists' | 'rejected';
    error: string;
  };
  type ProviderCount = { id: string; name: string; count: number };
  type SourcePreview = {
    source: Source;
    newCount: number;
    existsCount: number;
    rejectedCount: number;
    byProvider: ProviderCount[];
    rows: PreviewRow[];
  };
  type ImportRow = {
    key_masked: string;
    provider: string;
    status: string;
    error: string;
  };
  type SubmitSummary = {
    accepted: number;
    rejected: number;
    hasPending: boolean;
    rows: ImportRow[];
  };

  const MAX_FILE_BYTES = 1024 * 1024;
  const MAX_FILES = 20;
  const AUTO_PREVIEW_CHARS = 4096;
  const TYPING_DEBOUNCE_MS = 900;
  const PASTE_DEBOUNCE_MS = 250;
  const VALIDATION_POLL_MS = 3000;
  const VALIDATION_POLL_LIMIT = 60;

  const FORMAT_LABELS: Record<string, string> = {
    api_key: 'API key',
    codex_auth_json: 'Codex login',
    antigravity_json: 'Antigravity login',
    oauth_json: 'OAuth login',
    unknown: 'Unrecognized'
  };
  const PREVIEW_STATUS_LABELS: Record<PreviewRow['status'], string> = {
    new: 'New',
    exists: 'Already stored',
    rejected: 'Rejected'
  };
  const IMPORT_STATUS_LABELS: Record<string, string> = {
    pending_validation: 'Validating',
    active: 'Active',
    exists: 'Already stored',
    unidentified: 'Needs a provider',
    rejected: 'Rejected'
  };

  let keysText = '';
  let files: AttachedFile[] = [];
  let fileInput: HTMLInputElement;
  let dragging = false;
  let fileError = '';
  let providerId = 'auto';
  let customBaseUrl = '';
  let provisionRouting = true;
  let catalogProviders: JsonObject[] = [];
  let catalogError = '';
  let loadingProviders = false;

  let previews: SourcePreview[] = [];
  let previewSignature = '';
  let previewing = false;
  let previewError = '';
  let previewRun = 0;
  let debounce: ReturnType<typeof setTimeout> | undefined;

  let importing = false;
  let importError = '';
  let imported: SubmitSummary | undefined;
  let pendingCount = 0;
  let validationState: 'idle' | 'running' | 'settled' | 'timeout' = 'idle';
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let pollController: AbortController | undefined;
  let fileSeq = 0;

  $: fallbackProviders = firstList(data, 'provider_cards', 'providers');
  $: providers = catalogProviders.length ? catalogProviders : fallbackProviders;

  let providerPreselected = false;

  $: if (!providerPreselected && providers.length) {
    providerPreselected = true;
    const requested = new URLSearchParams(window.location.search).get('provider') ?? '';
    if (
      requested &&
      providers.some((provider) => text(provider.id ?? provider.provider_id, '') === requested)
    ) {
      providerId = requested;
    }
  }
  $: sources = currentSources(keysText, files);
  $: signature = signatureFor(sources, providerId);
  $: hasInput = sources.length > 0;
  $: fresh = previews.length > 0 && previewSignature === signature;
  $: totals = fresh ? summarize(previews) : undefined;
  $: rows = fresh ? previews.flatMap((preview) => preview.rows) : [];
  $: newTotal = totals?.newCount ?? 0;
  $: canImport = fresh && newTotal > 0 && !importing && !previewing;
  $: multiSource = sources.length > 1;

  function currentSources(pasted: string, attached: AttachedFile[]): Source[] {
    const result: Source[] = [];
    if (pasted.trim()) result.push({ id: 'pasted', label: 'Pasted text', text: pasted });
    result.push(...attached);
    return result;
  }

  function signatureFor(items: Source[], provider: string): string {
    return `${provider}\u0000${items.map((item) => `${item.id}\u0001${item.text}`).join('\u0000')}`;
  }

  function providerOptionLabel(provider: JsonObject): string {
    const name = text(provider.display_name ?? provider.name ?? provider.id);
    const id = text(provider.id ?? provider.provider_id, '');
    const collisions = providers.filter(
      (peer) => text(peer.display_name ?? peer.name ?? peer.id) === name
    ).length;
    return collisions > 1 && id ? `${name} (${id})` : name;
  }

  function summarize(items: SourcePreview[]) {
    const byProvider = new Map<string, ProviderCount>();
    let newCount = 0;
    let existsCount = 0;
    let rejectedCount = 0;
    for (const item of items) {
      newCount += item.newCount;
      existsCount += item.existsCount;
      rejectedCount += item.rejectedCount;
      for (const group of item.byProvider) {
        const existing = byProvider.get(group.id);
        if (existing) existing.count += group.count;
        else byProvider.set(group.id, { ...group });
      }
    }
    return { newCount, existsCount, rejectedCount, byProvider: [...byProvider.values()] };
  }

  function previewStatus(value: unknown): PreviewRow['status'] {
    return value === 'new' || value === 'exists' ? value : 'rejected';
  }

  function parsePreview(source: Source, payload: unknown): SourcePreview {
    const result = object(payload);
    if (result.ok === false) throw new Error(text(result.error, 'Janus could not check these.'));
    const previewRows: PreviewRow[] = list(result.results).map((row) => ({
      source: source.label,
      key_masked: text(row.key_masked, '****'),
      label: text(row.label, ''),
      provider: text(row.provider_display_name ?? row.provider_id, ''),
      format: FORMAT_LABELS[text(row.format, 'unknown')] ?? FORMAT_LABELS.unknown,
      status: previewStatus(row.status),
      error: text(row.error, '')
    }));
    const byProvider = list(result.by_provider)
      .map((group) => ({
        id: text(group.provider_id, ''),
        name: text(group.provider_display_name ?? group.provider_id, 'Unknown'),
        count: number(group.count)
      }))
      .filter((group) => group.id && group.count > 0);
    return {
      source,
      newCount: number(result.new_count),
      existsCount: number(result.exists_count),
      rejectedCount: number(result.rejected_count),
      byProvider,
      rows: previewRows
    };
  }

  function formFor(source: Source, includeImportFields: boolean): FormData {
    const body = new FormData();
    body.set('keys_text', source.text);
    body.set('provider_id', providerId);
    if (includeImportFields) {
      body.set('custom_base_url', customBaseUrl.trim());
      if (provisionRouting) body.set('provision_routing', 'true');
    }
    return body;
  }

  function friendlyError(error: unknown, fallback: string): string {
    const message = error instanceof Error ? error.message : '';
    return !message || message.startsWith('{') || message.startsWith('[') ? fallback : message;
  }

  async function runPreview() {
    clearTimeout(debounce);
    debounce = undefined;
    const snapshot = currentSources(keysText, files);
    const snapshotSignature = signatureFor(snapshot, providerId);
    if (!snapshot.length) {
      previews = [];
      previewError = '';
      return;
    }
    const run = ++previewRun;
    previewing = true;
    previewError = '';
    try {
      const results: SourcePreview[] = [];
      for (const source of snapshot) {
        const payload = await mutate('/dashboard/api/inventory/preview', {
          body: formFor(source, false)
        });
        if (run !== previewRun) return;
        results.push(parsePreview(source, payload));
      }
      previews = results;
      previewSignature = snapshotSignature;
    } catch (error) {
      if (run !== previewRun) return;
      previews = [];
      previewSignature = '';
      previewError = friendlyError(error, 'Janus could not check these credentials.');
    } finally {
      if (run === previewRun) previewing = false;
    }
  }

  function schedulePreview(delay: number) {
    clearTimeout(debounce);
    debounce = setTimeout(() => void runPreview(), delay);
  }

  function onInput() {
    imported = undefined;
    const size = currentSources(keysText, files).reduce(
      (total, source) => total + source.text.length,
      0
    );
    if (size <= AUTO_PREVIEW_CHARS) schedulePreview(TYPING_DEBOUNCE_MS);
    else clearTimeout(debounce);
  }

  function onPaste() {
    imported = undefined;
    schedulePreview(PASTE_DEBOUNCE_MS);
  }

  async function addFiles(list: FileList | File[] | null | undefined) {
    fileError = '';
    const incoming = Array.from(list ?? []);
    if (!incoming.length) return;
    const accepted: AttachedFile[] = [];
    const problems: string[] = [];
    for (const file of incoming) {
      if (files.length + accepted.length >= MAX_FILES) {
        problems.push(`Only ${MAX_FILES} files can be added at once.`);
        break;
      }
      if (file.size > MAX_FILE_BYTES) {
        problems.push(`${file.name} is larger than 1 MB.`);
        continue;
      }
      try {
        const content = await readFile(file);
        if (!content.trim()) {
          problems.push(`${file.name} is empty.`);
          continue;
        }
        accepted.push({
          id: `file-${++fileSeq}`,
          label: file.name,
          text: content,
          size: file.size
        });
      } catch {
        problems.push(`${file.name} could not be read.`);
      }
    }
    fileError = problems.join(' ');
    if (accepted.length) {
      imported = undefined;
      files = [...files, ...accepted];
      schedulePreview(0);
    }
  }

  function readFile(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
      reader.onerror = () => reject(reader.error ?? new Error('read failed'));
      reader.readAsText(file);
    });
  }

  function removeFile(id: string) {
    files = files.filter((file) => file.id !== id);
    if (currentSources(keysText, files).length) schedulePreview(0);
    else previews = [];
  }

  function onDrop(event: DragEvent) {
    dragging = false;
    const dropped = event.dataTransfer?.files;
    if (dropped?.length) {
      event.preventDefault();
      void addFiles(dropped);
    }
  }

  function onDragOver(event: DragEvent) {
    if (Array.from(event.dataTransfer?.types ?? []).includes('Files')) {
      event.preventDefault();
      dragging = true;
    }
  }

  function clearAll() {
    clearTimeout(debounce);
    previewRun += 1;
    keysText = '';
    files = [];
    previews = [];
    previewSignature = '';
    previewError = '';
    previewing = false;
    fileError = '';
  }

  function parseSubmit(payload: unknown): SubmitSummary {
    const result = object(payload);
    const importRows: ImportRow[] = list(result.results).map((row) => ({
      key_masked: text(row.key_masked, '****'),
      provider: text(row.provider_display_name ?? row.provider_id, ''),
      status: text(row.status, 'rejected'),
      error: text(row.error, '')
    }));
    const accepted = number(result.accepted_count);
    if (accepted < 1 && !importRows.length)
      throw new Error('Janus did not confirm that any credentials were stored.');
    return {
      accepted,
      rejected: number(result.rejected_count),
      hasPending: result.has_pending === true,
      rows: importRows
    };
  }

  async function runImport() {
    if (!canImport) return;
    const targets = previews.filter((preview) => preview.newCount > 0);
    importing = true;
    importError = '';
    const summary: SubmitSummary = { accepted: 0, rejected: 0, hasPending: false, rows: [] };
    try {
      for (const target of targets) {
        const count = target.newCount;
        const result = await action('/dashboard/api/inventory/submit', {
          body: formFor(target.source, true),
          success:
            targets.length > 1
              ? `Imported ${count} from ${target.source.label}`
              : `Imported ${count} credential${count === 1 ? '' : 's'}`,
          refresh: false,
          validate: parseSubmit
        });
        const part = result as SubmitSummary;
        summary.accepted += part.accepted;
        summary.rejected += part.rejected;
        summary.hasPending ||= part.hasPending;
        summary.rows.push(...part.rows);
        if (target.source.id === 'pasted') keysText = '';
        else files = files.filter((file) => file.id !== target.source.id);
      }
    } catch (error) {
      importError = friendlyError(error, 'None of the credentials were accepted.');
    } finally {
      importing = false;
    }
    if (summary.rows.length || summary.accepted) {
      imported = summary;
      previews = [];
      previewSignature = '';
      if (summary.hasPending) startValidationPolling();
      else validationState = 'settled';
    }
  }

  function startValidationPolling() {
    stopValidationPolling();
    validationState = 'running';
    let attempts = 0;
    const tickPoll = async () => {
      attempts += 1;
      pollController = new AbortController();
      try {
        const state = await getState('inventory', pollController.signal);
        pendingCount = number(object(state.data.summary).pending);
      } catch {
        pendingCount = Math.max(pendingCount, 1);
      }
      if (validationState !== 'running') return;
      if (pendingCount === 0) validationState = 'settled';
      else if (attempts >= VALIDATION_POLL_LIMIT) validationState = 'timeout';
      else pollTimer = setTimeout(() => void tickPoll(), VALIDATION_POLL_MS);
    };
    pollTimer = setTimeout(() => void tickPoll(), VALIDATION_POLL_MS);
  }

  function stopValidationPolling() {
    clearTimeout(pollTimer);
    pollController?.abort();
  }

  function formatSize(bytes: number): string {
    return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
  }

  onMount(async () => {
    loadingProviders = true;
    try {
      const response = await dashboardFetch('/dashboard/api/inventory/providers', {
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      catalogProviders = list(object(payload).providers);
    } catch (error) {
      catalogError =
        error instanceof Error ? error.message : 'The provider list could not be loaded.';
    } finally {
      loadingProviders = false;
    }
  });

  onDestroy(() => {
    clearTimeout(debounce);
    stopValidationPolling();
    validationState = 'idle';
  });
</script>

<PageHeader
  title="Connect credentials"
  description="Paste API keys or drop CLI login files. Janus works out the provider and shows what it found before anything is stored."
/>

<div class="connect-layout">
  <section class="panel connect-panel">
    <div
      class="drop-zone"
      class:dragging
      role="group"
      aria-label="Credentials to connect"
      on:dragover={onDragOver}
      on:dragleave={() => (dragging = false)}
      on:drop={onDrop}
    >
      <label class="sr-only" for="connect-keys">API keys or login file contents</label>
      <textarea
        id="connect-keys"
        name="keys_text"
        rows="9"
        bind:value={keysText}
        on:input={onInput}
        on:paste={onPaste}
        autocomplete="off"
        spellcheck="false"
        placeholder={'Paste keys here, one per line\n\nsk-proj-…\ngsk_…\nsk-or-v1-…'}></textarea>
      <div class="drop-footer">
        {#if files.length}
          <ul class="file-chips" aria-label="Attached files">
            {#each files as file (file.id)}<li>
                <Icon name="vault" size={14} />
                <span class="file-name">{file.label}</span>
                <small>{formatSize(file.size)}</small>
                <button
                  type="button"
                  class="chip-remove"
                  aria-label={`Remove ${file.label}`}
                  on:click={() => removeFile(file.id)}
                >
                  <Icon name="x" size={12} />
                </button>
              </li>{/each}
          </ul>
        {/if}
        <p class="drop-hint">
          <Icon name="upload" size={16} />
          <span>Drop login files here, or</span>
          <button type="button" class="link-button" on:click={() => fileInput.click()}>
            choose files
          </button>
        </p>
        <input
          bind:this={fileInput}
          class="sr-only"
          type="file"
          multiple
          tabindex="-1"
          aria-hidden="true"
          accept=".json,.txt,application/json,text/plain"
          on:change={(event) => {
            void addFiles(event.currentTarget.files);
            event.currentTarget.value = '';
          }}
        />
      </div>
    </div>
    {#if fileError}<p class="inline-error" role="alert">{fileError}</p>{/if}

    <div class="connect-options">
      <label class="field">
        <span>Provider</span>
        <select
          bind:value={providerId}
          disabled={loadingProviders}
          on:change={() => hasInput && schedulePreview(0)}
        >
          <option value="auto">Detect automatically</option>
          {#each providers as provider}<option
              value={text(provider.id ?? provider.provider_id, '')}
            >
              {providerOptionLabel(provider)}
            </option>{/each}
        </select>
        <small>
          {loadingProviders
            ? 'Loading providers…'
            : catalogError || 'Pick one only if detection gets it wrong.'}
        </small>
      </label>
      <label class="toggle-row">
        <input type="checkbox" bind:checked={provisionRouting} />
        <span>
          <strong>Make these routable</strong>
          <small>Set up routing for each provider so requests can use the new accounts.</small>
        </span>
      </label>
      <details class="advanced">
        <summary>Advanced</summary>
        <label class="field">
          <span>Custom base URL</span>
          <input
            type="url"
            bind:value={customBaseUrl}
            placeholder="https://api.example.com/v1"
            autocomplete="off"
          />
          <small>Only for self-hosted or OpenAI-compatible endpoints.</small>
        </label>
      </details>
    </div>

    <div class="preview" aria-live="polite">
      {#if previewing}
        <p class="preview-status">Checking {sources.length > 1 ? 'sources' : 'credentials'}…</p>
      {:else if previewError}
        <p class="inline-error" role="alert">{previewError}</p>
      {:else if totals}
        <ul class="summary-chips" aria-label="What Janus found">
          {#each totals.byProvider as group (group.id)}<li class="chip provider-chip">
              {group.name}
              <b>×{group.count}</b>
            </li>{/each}
          {#if totals.existsCount}<li class="chip exists-chip">
              {totals.existsCount} already stored
            </li>{/if}
          {#if totals.rejectedCount}<li class="chip rejected-chip">
              {totals.rejectedCount} rejected
            </li>{/if}
          {#if !totals.byProvider.length && !totals.existsCount && !totals.rejectedCount}<li
              class="chip"
            >
              Nothing recognized
            </li>{/if}
        </ul>
        {#if rows.length}
          <div class="table-wrap preview-table">
            <table>
              <thead>
                <tr>
                  {#if multiSource}<th>Source</th>{/if}
                  <th>Credential</th>
                  <th>Provider</th>
                  <th>Type</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {#each rows as row, index (index)}<tr>
                    {#if multiSource}<td class="muted">{row.source}</td>{/if}
                    <td>
                      <code class="mono">{row.key_masked}</code>
                      {#if row.label}<small class="row-label">{row.label}</small>{/if}
                    </td>
                    <td>
                      {row.provider ||
                        (row.status === 'new' ? 'Detected on import' : 'Not detected')}
                    </td>
                    <td class="muted">{row.format}</td>
                    <td>
                      <span
                        class="status {row.status === 'new'
                          ? 'active'
                          : row.status === 'rejected'
                            ? 'error'
                            : ''}"
                      >
                        {PREVIEW_STATUS_LABELS[row.status]}
                      </span>
                      {#if row.error}<small class="row-error">{row.error}</small>{/if}
                    </td>
                  </tr>{/each}
              </tbody>
            </table>
          </div>
        {/if}
      {:else if hasInput}
        <p class="preview-status">Check the credentials to see what Janus will import.</p>
      {/if}
    </div>

    {#if importError}<p class="inline-error" role="alert">{importError}</p>{/if}

    <div class="connect-actions">
      <button type="button" class="button ghost" disabled={!hasInput} on:click={clearAll}>
        Clear
      </button>
      {#if hasInput && !fresh}
        <button
          type="button"
          class="button"
          disabled={previewing}
          on:click={() => void runPreview()}
        >
          <Icon name="search" size={15} />Check credentials
        </button>
      {/if}
      <button type="button" class="button primary" disabled={!canImport} on:click={runImport}>
        {importing
          ? 'Importing…'
          : newTotal
            ? `Import ${newTotal} credential${newTotal === 1 ? '' : 's'}`
            : 'Import'}
      </button>
    </div>
  </section>

  <aside class="connect-aside">
    {#if imported}
      <section class="panel result-panel" aria-live="polite">
        <div class="panel-header">
          <div>
            <h2>
              {imported.accepted} credential{imported.accepted === 1 ? '' : 's'} imported
            </h2>
            <p>
              {#if validationState === 'running'}
                Checking {pendingCount || 'the new'} account{pendingCount === 1 ? '' : 's'} with each
                provider…
              {:else if validationState === 'timeout'}
                Validation is still running in the background.
              {:else}
                Validation finished.
              {/if}
            </p>
          </div>
          <span
            class="status {validationState === 'running' || validationState === 'timeout'
              ? 'pending'
              : 'active'}"
          >
            {validationState === 'running' || validationState === 'timeout' ? 'Validating' : 'Done'}
          </span>
        </div>
        {#if imported.rows.length}
          <ul class="result-rows">
            {#each imported.rows as row, index (index)}<li>
                <code class="mono">{row.key_masked}</code>
                <span class="muted">{row.provider || 'Not detected'}</span>
                <span
                  class="status {row.status === 'rejected'
                    ? 'error'
                    : row.status === 'pending_validation'
                      ? 'pending'
                      : 'active'}"
                >
                  {IMPORT_STATUS_LABELS[row.status] ?? row.status}
                </span>
                {#if row.error}<small class="row-error">{row.error}</small>{/if}
              </li>{/each}
          </ul>
        {/if}
        <div class="next-steps">
          <button class="button" on:click={() => navigate('/dashboard/ui/inventory/keys')}>
            <Icon name="vault" size={15} />View in Inventory
          </button>
          <button class="button" on:click={() => navigate('/dashboard/ui/keys')}>
            <Icon name="key" size={15} />Create a client key
          </button>
          <button class="button" on:click={() => navigate('/dashboard/ui/tools')}>
            <Icon name="tool" size={15} />Send a test request
          </button>
        </div>
      </section>
    {/if}
    <section class="accepts">
      <h2>What you can add</h2>
      <dl>
        <div>
          <dt>API keys</dt>
          <dd>One per line. OpenAI, Anthropic, Groq, OpenRouter, Gemini and others.</dd>
        </div>
        <div>
          <dt>Codex CLI login</dt>
          <dd>
            <code>~/.codex/auth.json</code>
            , or a 9router
            <code>providerConnections</code>
            export
          </dd>
        </div>
        <div>
          <dt>Cline account</dt>
          <dd>
            A <code>workos:</code>
            token, or JSON with
            <code>"provider": "cline"</code>
          </dd>
        </div>
        <div>
          <dt>Antigravity and Kiro</dt>
          <dd>OAuth credential JSON with access and refresh tokens.</dd>
        </div>
        <div>
          <dt>Claude Code login</dt>
          <dd>Not stored here. Add it under Routing → Providers as Claude OAuth.</dd>
        </div>
      </dl>
      <p>
        Nothing is stored until you choose Import. Stored credentials stay masked in the dashboard.
      </p>
    </section>
  </aside>
</div>

<style>
  .connect-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.7fr) minmax(260px, 0.75fr);
    gap: var(--space-5);
    align-items: start;
  }
  .connect-panel {
    padding: var(--space-5);
    overflow: visible;
  }
  .drop-zone {
    border: 1.5px dashed var(--line-strong);
    border-radius: var(--radius-md);
    background: var(--surface-soft);
    transition:
      border-color 0.15s ease,
      background-color 0.15s ease;
  }
  .drop-zone:focus-within {
    border-color: var(--accent);
    border-style: solid;
  }
  .drop-zone.dragging {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .drop-zone textarea {
    display: block;
    width: 100%;
    min-height: 210px;
    padding: var(--space-4);
    border: 0;
    border-radius: var(--radius-md) var(--radius-md) 0 0;
    background: transparent;
    color: var(--text);
    resize: vertical;
    font:
      13px/1.6 ui-monospace,
      SFMono-Regular,
      Menlo,
      monospace;
  }
  .drop-zone textarea:focus {
    outline: none;
  }
  .drop-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2) var(--space-4);
    padding: var(--space-3) var(--space-4);
    border-top: 1px solid var(--line);
  }
  .drop-hint {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 0 auto;
    color: var(--muted);
    font-size: 13px;
  }
  .link-button {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent-strong);
    font-weight: 650;
    text-decoration: underline;
    text-underline-offset: 3px;
    cursor: pointer;
  }
  .file-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .file-chips li {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 4px 4px 9px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface-solid);
    font-size: 12px;
  }
  .file-name {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 620;
  }
  .file-chips small {
    color: var(--muted);
  }
  .chip-remove {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .chip-remove:hover {
    color: var(--danger);
    background: var(--danger-soft);
  }
  .connect-options {
    display: grid;
    grid-template-columns: minmax(200px, 1fr) minmax(0, 1.3fr);
    gap: var(--space-4) var(--space-5);
    align-items: start;
    margin-top: var(--space-5);
  }
  .toggle-row {
    display: flex;
    gap: var(--space-3);
    align-items: flex-start;
    padding-top: 22px;
    cursor: pointer;
  }
  .toggle-row input {
    margin-top: 3px;
    accent-color: var(--accent);
  }
  .toggle-row strong,
  .toggle-row small {
    display: block;
  }
  .toggle-row strong {
    font-size: 13px;
  }
  .toggle-row small {
    margin-top: 2px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  .advanced {
    grid-column: 1 / -1;
  }
  .advanced summary {
    width: max-content;
    color: var(--muted);
    font-size: 13px;
    font-weight: 620;
    cursor: pointer;
  }
  .advanced[open] summary {
    margin-bottom: var(--space-3);
  }
  .advanced .field {
    max-width: 420px;
  }
  .preview {
    margin-top: var(--space-5);
  }
  .preview:empty {
    display: none;
  }
  .preview-status {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
  }
  .summary-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .chip {
    padding: 5px 10px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--surface-solid);
    font-size: 13px;
    font-weight: 600;
  }
  .chip b {
    color: var(--accent-strong);
    font-variant-numeric: tabular-nums;
  }
  .provider-chip {
    border-color: color-mix(in srgb, var(--accent) 35%, var(--line));
  }
  .exists-chip {
    color: var(--muted);
  }
  .rejected-chip {
    color: var(--danger);
    border-color: color-mix(in srgb, var(--danger) 30%, var(--line));
  }
  .preview-table {
    margin-top: var(--space-4);
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
  }
  .row-label,
  .row-error {
    display: block;
    margin-top: 3px;
    font-size: 12px;
    color: var(--muted);
  }
  .row-error {
    color: var(--danger);
  }
  .inline-error {
    margin: var(--space-3) 0 0;
    color: var(--danger);
    font-size: 13px;
  }
  .connect-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
    margin-top: var(--space-5);
    padding-top: var(--space-4);
    border-top: 1px solid var(--line);
  }
  .connect-actions .ghost {
    margin-right: auto;
  }
  .connect-aside {
    display: grid;
    gap: var(--space-4);
  }
  .accepts {
    padding: var(--space-1) var(--space-2);
  }
  .accepts h2 {
    margin: 0 0 var(--space-3);
    font-size: 15px;
  }
  .accepts dl {
    display: grid;
    gap: var(--space-3);
    margin: 0;
  }
  .accepts dt {
    font-size: 13px;
    font-weight: 650;
  }
  .accepts dd {
    margin: 2px 0 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.45;
  }
  .accepts code {
    font-size: 12px;
    color: var(--text);
  }
  .accepts p {
    margin: var(--space-4) 0 0;
    padding-top: var(--space-3);
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .result-rows {
    display: grid;
    gap: var(--space-2);
    margin: 0;
    padding: var(--space-4);
    list-style: none;
    font-size: 12px;
  }
  .result-rows li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px var(--space-2);
  }
  .result-rows .status {
    margin-left: auto;
  }
  .result-rows .row-error {
    flex-basis: 100%;
    margin: 0;
  }
  .next-steps {
    display: grid;
    gap: var(--space-2);
    padding: 0 var(--space-4) var(--space-4);
  }
  .next-steps .button {
    justify-content: flex-start;
  }
  @media (max-width: 980px) {
    .connect-layout {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 620px) {
    .connect-panel {
      padding: var(--space-4);
    }
    .connect-options {
      grid-template-columns: 1fr;
    }
    .toggle-row {
      padding-top: 0;
    }
    .drop-hint {
      margin-left: 0;
    }
  }
</style>
