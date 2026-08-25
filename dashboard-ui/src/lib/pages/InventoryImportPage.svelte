<script lang="ts">
  import type { ValidatedMutationOptions } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { compact, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: ValidatedMutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;

  let selectedFile: File | undefined;
  let dragging = false;
  let verify = true;
  let importing = false;
  let importedFilename = '';
  let importedCount = 0;
  let previewCount: number | undefined;
  let previewError = '';
  let importError = '';

  $: summary = object(data.summary);

  const tabs = [
    { label: 'Overview', href: '/dashboard/ui/inventory' },
    { label: 'All keys', href: '/dashboard/ui/inventory/keys' },
    { label: 'Add keys', href: '/dashboard/ui/inventory/add' },
    { label: 'Import JSON', href: '/dashboard/ui/inventory/import' }
  ];

  const wrappedExample = `{
  "keys": [
    {
      "key_value": "sk-proj-…",
      "provider_id": "openai",
      "key_label": "work account",
      "priority": 0
    }
  ]
}`;

  const bareExample = `[
  {
    "key": "gsk_…",
    "provider_id": "groq"
  },
  {
    "key_value": "sk-or-…",
    "source_node": "laptop-1"
  }
]`;

  async function choose(file: File | undefined) {
    selectedFile = file;
    importedFilename = '';
    importedCount = 0;
    previewCount = undefined;
    previewError = '';
    importError = '';
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.json') && file.type !== 'application/json') {
      previewError = 'Choose a JSON export file.';
      return;
    }
    try {
      const parsed: unknown = JSON.parse(await file.text());
      const root = object(parsed);
      const records = Array.isArray(parsed)
        ? parsed
        : Array.isArray(root.keys)
          ? root.keys
          : undefined;
      if (!records) throw new Error('Expected a top-level array or a keys array.');
      previewCount = records.length;
    } catch (error) {
      previewError = error instanceof Error ? error.message : 'The file is not valid JSON.';
    }
  }

  function drop(event: DragEvent) {
    dragging = false;
    const file = event.dataTransfer?.files[0];
    void choose(file);
  }

  function successfulImportCount(payload: unknown): number {
    if (payload !== null && typeof payload === 'object' && !Array.isArray(payload)) {
      const result = object(payload);
      if (result.ok !== true)
        throw new Error(text(result.error, 'Janus rejected the inventory import.'));
      const count = number(result.imported_count, -1);
      if (count < 1) throw new Error('Janus did not confirm that the inventory import completed.');
      return count;
    }
    if (typeof payload !== 'string')
      throw new Error('Janus returned an unexpected import response.');
    const document = new DOMParser().parseFromString(payload, 'text/html');
    const errorNode = document.querySelector('[class*="bg-red"]');
    if (errorNode?.textContent?.trim())
      throw new Error(errorNode.textContent.replace(/\s+/g, ' ').trim());
    const body = document.body.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    const count = body.match(/Imported\s+(\d+)\s+key\(s\)/i)?.[1];
    if (count === undefined)
      throw new Error('Janus did not confirm that the inventory import completed.');
    return Number(count);
  }

  async function submit() {
    if (!selectedFile || previewError) return;
    importing = true;
    importedFilename = '';
    importedCount = 0;
    importError = '';
    try {
      const body = new FormData();
      body.set('export_file', selectedFile);
      if (verify) body.set('verify', 'true');
      const result = await action('/dashboard/api/inventory/import', {
        body,
        success: 'Inventory import completed',
        refresh: false,
        validate: successfulImportCount
      });
      if (typeof result !== 'number') {
        importError =
          'Janus did not confirm that the inventory import completed. The selected file has been preserved.';
        return;
      }
      importedCount = result;
      importedFilename = selectedFile.name;
    } catch (error) {
      importError = error instanceof Error ? error.message : 'The inventory import failed.';
    } finally {
      importing = false;
    }
  }
</script>

<PageHeader
  title="Import credentials"
  description="Bring a JSON export from another Janus node or compatible key manager into this inventory."
>
  <a class="button" href="/dashboard/api/inventory/export" download>
    <Icon name="download" />Export current inventory
  </a>
</PageHeader>

<nav class="inventory-tabs" aria-label="Credential inventory sections">
  {#each tabs as tab}<button
      class:active={tab.label === 'Import JSON'}
      on:click={() => navigate(tab.href)}
    >
      {tab.label}
    </button>{/each}
</nav>

{#if importedFilename}
  <section class="success-banner">
    <span><Icon name="check" size={18} /></span>
    <div>
      <strong>Import completed</strong>
      <p>
        {importedFilename} was processed successfully. Janus imported {importedCount} credential{importedCount ===
        1
          ? ''
          : 's'}.
      </p>
    </div>
    <button class="button" on:click={() => navigate('/dashboard/ui/inventory/keys')}>
      Review credentials
    </button>
  </section>
{/if}

<div class="import-layout">
  <section class="panel upload-panel">
    <div class="panel-header">
      <div>
        <h2>Upload export</h2>
        <p>JSON is processed locally by this Janus node.</p>
      </div>
      {#if summary.total != null}<span class="status active">
          {compact(summary.total)} currently stored
        </span>{/if}
    </div>
    <div class="panel-body">
      <label
        class="drop-zone"
        class:dragging
        class:has-file={!!selectedFile && !previewError}
        on:dragover|preventDefault={() => (dragging = true)}
        on:dragleave={() => (dragging = false)}
        on:drop|preventDefault={drop}
      >
        <input
          type="file"
          accept=".json,application/json"
          on:change={(event) => choose((event.currentTarget as HTMLInputElement).files?.[0])}
        />
        <span class="upload-icon">
          <Icon name={selectedFile ? 'check' : 'download'} size={22} />
        </span>
        {#if selectedFile}<strong>{selectedFile.name}</strong>
          <p>
            {compact(selectedFile.size)} bytes{previewCount != null
              ? ` · ${previewCount} record${previewCount === 1 ? '' : 's'} detected`
              : ''}
          </p>
          <span class="button">Choose a different file</span>{:else}<strong>
            Drop a JSON export here
          </strong>
          <p>or select a file from this machine</p>
          <span class="button primary">Choose JSON file</span>{/if}
      </label>
      {#if previewError}<div class="file-error">
          <Icon name="warning" size={16} />
          <span>
            <strong>Could not preview this export</strong>
            {previewError}
          </span>
        </div>{/if}
      {#if importError}<div class="file-error">
          <Icon name="warning" size={16} />
          <span>
            <strong>Import failed</strong>
            {importError}
          </span>
        </div>{/if}
      <label class="verify-option">
        <input type="checkbox" bind:checked={verify} />
        <span>
          <strong>Verify after import</strong>
          <small>Run integrity, routing readiness, and encryption checks after ingestion.</small>
        </span>
      </label>
      <div class="import-actions">
        <button
          class="button primary"
          disabled={!selectedFile || !!previewError || importing}
          on:click={submit}
        >
          {importing
            ? 'Importing…'
            : `Import ${previewCount == null ? '' : previewCount} credential${previewCount === 1 ? '' : 's'}`}
        </button>
      </div>
    </div>
  </section>

  <aside class="format-card">
    <span class="eyebrow">Supported shape</span>
    <h2>Portable and forgiving</h2>
    <p>
      Use a wrapped <code>keys</code>
      array or a bare array. Each record needs either
      <code>key_value</code>
      or its
      <code>key</code>
      alias.
    </p>
    <div class="format-list">
      <div>
        <span>Required</span>
        <code>key_value</code>
        <small>Alias: key</small>
      </div>
      <div>
        <span>Recommended</span>
        <code>provider_id</code>
        <small>Auto-detected when omitted</small>
      </div>
      <div>
        <span>Optional</span>
        <code>key_label</code>
        <small>Friendly dashboard label</small>
      </div>
      <div>
        <span>Optional</span>
        <code>priority</code>
        <small>Routing order, default 0</small>
      </div>
    </div>
    <div class="privacy-note">
      <Icon name="vault" size={17} />
      <p>
        Duplicate credentials are skipped or updated using their secure hash. Imported values are
        revalidated before routing.
      </p>
    </div>
  </aside>
</div>

<section class="examples-section">
  <div class="section-heading">
    <div>
      <span class="eyebrow">Examples</span>
      <h2>Accepted JSON formats</h2>
    </div>
    <p>
      Existing status and health metadata may be included, but Janus verifies the credentials again.
    </p>
  </div>
  <div class="example-grid">
    <article class="panel">
      <div class="panel-header">
        <div>
          <h2>Wrapped export</h2>
          <p>Recommended for portable backups</p>
        </div>
        <span class="status active">keys[]</span>
      </div>
      <pre>{wrappedExample}</pre>
    </article>
    <article class="panel">
      <div class="panel-header">
        <div>
          <h2>Bare array</h2>
          <p>Useful for quick migrations</p>
        </div>
        <span class="status">[]</span>
      </div>
      <pre>{bareExample}</pre>
    </article>
  </div>
</section>

<style>
  .inventory-tabs {
    display: flex;
    gap: 5px;
    width: max-content;
    max-width: 100%;
    padding: 4px;
    margin: -10px 0 22px;
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--surface);
  }
  .inventory-tabs button {
    padding: 8px 13px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--muted);
    font-size: 11px;
    font-weight: 680;
    cursor: pointer;
    white-space: nowrap;
  }
  .inventory-tabs button:hover {
    color: var(--text);
    background: var(--surface-soft);
  }
  .inventory-tabs button.active {
    color: var(--accent-strong);
    background: var(--accent-soft);
  }
  .success-banner {
    display: flex;
    align-items: center;
    gap: 13px;
    padding: 15px 17px;
    margin-bottom: 18px;
    border: 1px solid color-mix(in srgb, var(--success) 22%, var(--line));
    border-radius: 15px;
    background: color-mix(in srgb, var(--success) 7%, var(--surface));
  }
  .success-banner > span {
    display: grid;
    place-items: center;
    width: 35px;
    height: 35px;
    flex: 0 0 auto;
    border-radius: 11px;
    color: var(--success);
    background: color-mix(in srgb, var(--success) 14%, transparent);
  }
  .success-banner > div {
    flex: 1;
  }
  .success-banner strong {
    font-size: 12px;
  }
  .success-banner p {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.45;
  }
  .import-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.45fr) minmax(270px, 0.7fr);
    gap: 18px;
    align-items: start;
  }
  .upload-panel {
    overflow: visible;
  }
  .drop-zone {
    display: grid;
    place-items: center;
    min-height: 270px;
    padding: 27px;
    border: 1px dashed var(--line-strong);
    border-radius: 15px;
    background: linear-gradient(
      145deg,
      color-mix(in srgb, var(--accent-soft) 22%, var(--surface-solid)),
      var(--surface-soft)
    );
    text-align: center;
    cursor: pointer;
    transition: 0.18s ease;
  }
  .drop-zone:hover,
  .drop-zone.dragging {
    border-color: var(--accent);
    background: var(--accent-soft);
    transform: translateY(-1px);
  }
  .drop-zone.has-file {
    border-color: color-mix(in srgb, var(--success) 45%, var(--line));
    background: color-mix(in srgb, var(--success) 5%, var(--surface-soft));
  }
  .drop-zone input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
  }
  .upload-icon {
    display: grid;
    place-items: center;
    width: 48px;
    height: 48px;
    margin-bottom: 12px;
    border-radius: 15px;
    color: white;
    background: linear-gradient(145deg, var(--accent), var(--cyan));
    box-shadow: 0 10px 24px color-mix(in srgb, var(--accent) 20%, transparent);
  }
  .has-file .upload-icon {
    background: linear-gradient(145deg, var(--success), var(--accent));
  }
  .drop-zone strong {
    font-size: 13px;
  }
  .drop-zone p {
    margin: 5px 0 15px;
    color: var(--muted);
    font-size: 10px;
  }
  .file-error {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    padding: 11px 13px;
    margin-top: 11px;
    border: 1px solid color-mix(in srgb, var(--danger) 20%, var(--line));
    border-radius: 11px;
    color: var(--danger);
    background: var(--danger-soft);
    font-size: 10px;
  }
  .file-error span,
  .file-error strong {
    display: block;
  }
  .file-error strong {
    margin-bottom: 2px;
  }
  .verify-option {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-top: 15px;
    padding: 13px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--surface-soft);
    cursor: pointer;
  }
  .verify-option input {
    margin-top: 2px;
    accent-color: var(--accent);
  }
  .verify-option strong,
  .verify-option small {
    display: block;
  }
  .verify-option strong {
    font-size: 11px;
  }
  .verify-option small {
    margin-top: 3px;
    color: var(--muted);
    font-size: 9px;
  }
  .import-actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 15px;
  }
  .format-card {
    padding: 20px;
    border: 1px solid var(--line);
    border-radius: 17px;
    background: var(--surface);
    box-shadow: 0 4px 18px rgba(44, 85, 92, 0.035);
  }
  .format-card h2 {
    margin: 6px 0;
    font-size: 17px;
    letter-spacing: -0.025em;
  }
  .format-card > p {
    margin: 0;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.55;
  }
  .format-card code {
    color: var(--accent-strong);
    font:
      10px ui-monospace,
      monospace;
  }
  .format-list {
    display: grid;
    margin: 18px 0;
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
  }
  .format-list > div {
    display: grid;
    grid-template-columns: 74px 1fr auto;
    align-items: center;
    gap: 8px;
    padding: 10px;
    border-bottom: 1px solid var(--line);
  }
  .format-list > div:last-child {
    border-bottom: 0;
  }
  .format-list span {
    color: var(--faint);
    font-size: 8px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .format-list small {
    color: var(--muted);
    font-size: 8px;
    text-align: right;
  }
  .privacy-note {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    padding: 12px;
    border-radius: 11px;
    color: var(--accent-strong);
    background: var(--accent-soft);
  }
  .privacy-note p {
    margin: 0;
    color: var(--muted);
    font-size: 9px;
    line-height: 1.5;
  }
  .examples-section {
    margin-top: 25px;
  }
  .section-heading {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 20px;
    margin: 0 1px 13px;
  }
  .section-heading h2 {
    margin: 5px 0 0;
    font-size: 17px;
    letter-spacing: -0.02em;
  }
  .section-heading p {
    margin: 0;
    color: var(--muted);
    font-size: 10px;
  }
  .example-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 15px;
  }
  .example-grid pre {
    min-height: 250px;
    padding: 18px;
    margin: 0;
    overflow: auto;
    color: var(--muted);
    background: var(--surface-soft);
    font:
      10px/1.65 ui-monospace,
      monospace;
  }
  @media (max-width: 940px) {
    .import-layout {
      grid-template-columns: 1fr;
    }
    .format-list > div {
      grid-template-columns: 80px 1fr auto;
    }
  }
  @media (max-width: 650px) {
    .inventory-tabs {
      width: 100%;
      overflow-x: auto;
    }
    .success-banner {
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .success-banner > div {
      min-width: calc(100% - 50px);
    }
    .success-banner > .button {
      margin-left: 48px;
    }
    .example-grid {
      grid-template-columns: 1fr;
    }
    .section-heading {
      display: block;
    }
    .section-heading p {
      margin-top: 5px;
    }
    .drop-zone {
      min-height: 230px;
    }
    .format-list > div {
      grid-template-columns: 72px 1fr;
    }
    .format-list small {
      grid-column: 2;
      text-align: left;
    }
  }
</style>
