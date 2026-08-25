<script lang="ts">
  import { onMount } from 'svelte';
  import { dashboardFetch, responseError } from '$lib/api';
  import type { ValidatedMutationOptions } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { firstList, list, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: ValidatedMutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;

  let catalogProviders: JsonObject[] = [];
  let loadingProviders = false;
  let submitting = false;
  let submittedCount = 0;
  let keysText = '';
  let providerId = 'auto';
  let customBaseUrl = '';
  let provisionRouting = true;
  let submitError = '';
  let catalogError = '';

  $: fallbackProviders = firstList(data, 'provider_cards', 'providers');
  $: providers = catalogProviders.length ? catalogProviders : fallbackProviders;
  $: keyCount = keysText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean).length;

  const tabs = [
    { label: 'Overview', href: '/dashboard/ui/inventory' },
    { label: 'All keys', href: '/dashboard/ui/inventory/keys' },
    { label: 'Add keys', href: '/dashboard/ui/inventory/add' },
    { label: 'Import JSON', href: '/dashboard/ui/inventory/import' }
  ];

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
        error instanceof Error ? error.message : 'The provider catalog could not be loaded.';
    } finally {
      loadingProviders = false;
    }
  });

  function acceptedSubmissionCount(payload: unknown): number {
    if (payload !== null && typeof payload === 'object' && !Array.isArray(payload)) {
      const result = object(payload);
      const accepted = number(result.accepted_count);
      if (result.ok !== true) {
        const errors = firstList(result, 'results')
          .map((item) => text(item.error, ''))
          .filter(Boolean);
        throw new Error(
          errors.length
            ? `Submission was not fully accepted: ${[...new Set(errors)].join(' ')}`
            : text(result.error, 'Janus rejected one or more credentials.')
        );
      }
      if (accepted < 1)
        throw new Error('Janus did not confirm that any credentials were accepted.');
      return accepted;
    }
    if (typeof payload !== 'string')
      throw new Error('Janus returned an unexpected submission response.');
    const document = new DOMParser().parseFromString(payload, 'text/html');
    const errorNode = document.querySelector('[class*="bg-red"]');
    if (errorNode?.textContent?.trim())
      throw new Error(errorNode.textContent.replace(/\s+/g, ' ').trim());
    const rows = Array.from(document.querySelectorAll('tbody tr'));
    if (!rows.length) throw new Error('Janus did not confirm that any credentials were accepted.');
    const incomplete = rows
      .map(
        (row) => row.querySelector('td:last-child')?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      )
      .filter((status) => !/\b(pending_validation|active)\b/i.test(status));
    if (incomplete.length)
      throw new Error(`Submission was not fully queued: ${incomplete.join('; ')}`);
    const heading = Array.from(document.querySelectorAll('h2'))
      .map((node) => node.textContent?.trim() ?? '')
      .find((value) => /^Submitted\s+\d+\s+key/i.test(value));
    const count = heading?.match(/^Submitted\s+(\d+)/i)?.[1];
    if (!count) throw new Error('Janus did not confirm the submitted credential count.');
    return Number(count);
  }

  async function submit(event: SubmitEvent) {
    const form = event.currentTarget as HTMLFormElement;
    submitting = true;
    submitError = '';
    submittedCount = 0;
    try {
      const result = await action('/dashboard/api/inventory/submit', {
        body: new FormData(form),
        success: `${keyCount} credential${keyCount === 1 ? '' : 's'} queued for validation`,
        refresh: false,
        validate: acceptedSubmissionCount
      });
      if (typeof result !== 'number') {
        submitError =
          'Janus did not confirm that the credentials were accepted. Your input has been preserved.';
        return;
      }
      submittedCount = result;
      keysText = '';
    } catch (error) {
      submitError =
        error instanceof Error ? error.message : 'The credentials could not be submitted.';
    } finally {
      submitting = false;
    }
  }
</script>

<PageHeader
  title="Add upstream credentials"
  description="Paste one credential per line. Janus can identify known providers and validate accounts in the background."
>
  <button class="button" on:click={() => navigate('/dashboard/ui/inventory/keys')}>
    <Icon name="key" />View all keys
  </button>
</PageHeader>

<nav class="inventory-tabs" aria-label="Credential inventory sections">
  {#each tabs as tab}<button
      class:active={tab.label === 'Add keys'}
      on:click={() => navigate(tab.href)}
    >
      {tab.label}
    </button>{/each}
</nav>

{#if submittedCount > 0}
  <section class="success-banner">
    <span><Icon name="check" size={18} /></span>
    <div>
      <strong>{submittedCount} credential{submittedCount === 1 ? '' : 's'} added</strong>
      <p>
        Validation is running in the background. Routing will use credentials as soon as they become
        usable.
      </p>
    </div>
    <button
      class="button"
      on:click={() => navigate('/dashboard/ui/inventory/keys?status=pending_validation')}
    >
      Watch validation
    </button>
  </section>
{/if}

{#if submitError}
  <div class="file-error submit-error" role="alert">
    <Icon name="warning" size={16} />
    <span>
      <strong>Credentials were not fully accepted</strong>
      {submitError}
    </span>
  </div>
{/if}

<div class="add-layout">
  <section class="panel form-panel">
    <div class="panel-header">
      <div>
        <h2>Credentials</h2>
        <p>Secrets are masked after submission and never returned in dashboard state.</p>
      </div>
      <span class="status {keyCount ? 'active' : ''}">{keyCount} detected</span>
    </div>
    <form class="panel-body" on:submit|preventDefault={submit}>
      <label class="field full credential-field">
        <span>API keys or account credentials</span>
        <textarea
          name="keys_text"
          rows="12"
          bind:value={keysText}
          required
          autocomplete="off"
          spellcheck="false"
          placeholder={'sk-proj-…\nsk-or-v1-…\ngsk_…'}></textarea>
        <small>
          One value per line. Duplicate credentials are updated according to the existing ingestion
          rules.
        </small>
      </label>
      <div class="field-grid selector-grid">
        <label class="field">
          <span>Provider</span>
          <select name="provider_id" bind:value={providerId} disabled={loadingProviders}>
            <option value="auto">Auto-detect from credential</option>
            {#each providers as provider}<option
                value={text(provider.id ?? provider.provider_id, '')}
              >
                {text(provider.display_name ?? provider.name ?? provider.id)}
              </option>{/each}
          </select>
          <small>
            {loadingProviders
              ? 'Loading provider catalog…'
              : catalogError || 'Choose explicitly when the credential format is ambiguous.'}
          </small>
        </label>
        <label class="field">
          <span>Custom base URL</span>
          <input
            name="custom_base_url"
            type="url"
            bind:value={customBaseUrl}
            placeholder="https://api.example.com/v1"
          />
          <small>Only needed for custom or proxy-compatible providers.</small>
        </label>
      </div>
      <label class="routing-option">
        <input
          name="provision_routing"
          type="checkbox"
          value="true"
          bind:checked={provisionRouting}
        />
        <span class="option-icon"><Icon name="route" size={18} /></span>
        <span>
          <strong>Provision routing automatically</strong>
          <small>
            Create or update compatible routing providers after the credentials are stored.
          </small>
        </span>
      </label>
      <div class="form-actions">
        <button type="button" class="button" disabled={!keysText} on:click={() => (keysText = '')}>
          Clear
        </button>
        <button class="button primary" disabled={!keyCount || submitting}>
          {submitting
            ? 'Adding credentials…'
            : `Add ${keyCount || ''} credential${keyCount === 1 ? '' : 's'}`}
        </button>
      </div>
    </form>
  </section>

  <aside class="guidance-stack">
    <section class="guide-card accent-card">
      <span class="guide-icon"><Icon name="spark" /></span>
      <div>
        <span class="eyebrow">Smart detection</span>
        <h2>Let Janus classify them</h2>
        <p>
          Known credential prefixes and account payloads are matched to the provider catalog
          automatically.
        </p>
      </div>
    </section>
    <section class="guide-card">
      <span class="eyebrow">What happens next</span>
      <ol>
        <li>
          <span>1</span>
          <div>
            <strong>Stored securely</strong>
            <small>The credential is encrypted when storage encryption is configured.</small>
          </div>
        </li>
        <li>
          <span>2</span>
          <div>
            <strong>Validated upstream</strong>
            <small>Janus checks usability, available models, limits, and credits.</small>
          </div>
        </li>
        <li>
          <span>3</span>
          <div>
            <strong>Added to routing</strong>
            <small>Usable accounts become candidates for account-aware fallback.</small>
          </div>
        </li>
      </ol>
    </section>
    <section class="guide-card privacy-card">
      <Icon name="vault" size={18} />
      <div>
        <strong>Local control plane</strong>
        <p>
          Credentials stay on this Janus node. Future peer synchronization remains a separate,
          explicitly controlled capability.
        </p>
      </div>
    </section>
  </aside>
</div>

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
  .file-error {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    padding: 11px 13px;
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
  .submit-error {
    margin-bottom: 18px;
  }
  .add-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.55fr) minmax(270px, 0.72fr);
    gap: 18px;
    align-items: start;
  }
  .form-panel {
    overflow: visible;
  }
  .credential-field textarea {
    min-height: 250px;
    padding: 15px;
    font:
      12px/1.65 ui-monospace,
      SFMono-Regular,
      Menlo,
      monospace;
  }
  .selector-grid {
    margin-top: 17px;
  }
  .routing-option {
    display: flex;
    align-items: flex-start;
    gap: 11px;
    padding: 14px;
    margin-top: 17px;
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--surface-soft);
    cursor: pointer;
  }
  .routing-option > input {
    margin-top: 11px;
    accent-color: var(--accent);
  }
  .option-icon {
    display: grid;
    place-items: center;
    width: 37px;
    height: 37px;
    flex: 0 0 auto;
    border-radius: 11px;
    color: var(--accent-strong);
    background: var(--accent-soft);
  }
  .routing-option strong,
  .routing-option small {
    display: block;
  }
  .routing-option strong {
    margin-top: 2px;
    font-size: 12px;
  }
  .routing-option small {
    margin-top: 4px;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.45;
  }
  .guidance-stack {
    display: grid;
    gap: 13px;
  }
  .guide-card {
    padding: 18px;
    border: 1px solid var(--line);
    border-radius: 16px;
    background: var(--surface);
    box-shadow: 0 4px 18px rgba(44, 85, 92, 0.035);
  }
  .accent-card {
    display: flex;
    gap: 12px;
    background: linear-gradient(
      145deg,
      color-mix(in srgb, var(--accent-soft) 60%, var(--surface)),
      var(--surface)
    );
  }
  .guide-icon {
    display: grid;
    place-items: center;
    width: 38px;
    height: 38px;
    flex: 0 0 auto;
    border-radius: 12px;
    color: white;
    background: linear-gradient(145deg, var(--accent), var(--cyan));
  }
  .guide-card h2 {
    margin: 6px 0 5px;
    font-size: 15px;
    letter-spacing: -0.02em;
  }
  .guide-card p {
    margin: 0;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.55;
  }
  .guide-card ol {
    display: grid;
    gap: 17px;
    padding: 0;
    margin: 15px 0 0;
    list-style: none;
  }
  .guide-card li {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .guide-card li > span {
    display: grid;
    place-items: center;
    width: 25px;
    height: 25px;
    flex: 0 0 auto;
    border-radius: 8px;
    color: var(--accent-strong);
    background: var(--accent-soft);
    font-size: 10px;
    font-weight: 800;
  }
  .guide-card li strong,
  .guide-card li small {
    display: block;
  }
  .guide-card li strong {
    font-size: 11px;
  }
  .guide-card li small {
    margin-top: 3px;
    color: var(--muted);
    font-size: 9px;
    line-height: 1.45;
  }
  .privacy-card {
    display: flex;
    gap: 12px;
    color: var(--accent-strong);
  }
  .privacy-card div {
    color: var(--text);
  }
  .privacy-card strong {
    font-size: 11px;
  }
  @media (max-width: 980px) {
    .add-layout {
      grid-template-columns: 1fr;
    }
    .guidance-stack {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .privacy-card {
      grid-column: 1/-1;
    }
  }
  @media (max-width: 620px) {
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
    .guidance-stack {
      grid-template-columns: 1fr;
    }
    .privacy-card {
      grid-column: auto;
    }
    .credential-field textarea {
      min-height: 210px;
    }
  }
</style>
