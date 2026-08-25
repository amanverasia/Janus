<script lang="ts">
  import { dashboardFetch, responseError } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;

  let exporting = false;
  let exportError = '';

  $: settings = object(data.settings);

  const toggles = [
    ['server_require_api_key', 'Require API key'],
    ['server_cooldowns_enabled', 'Enable account cooldowns'],
    ['server_sticky_client_key_routing', 'Sticky client routing'],
    ['server_request_logging', 'Record request metadata']
  ];

  async function save(key: string, value: string) {
    const body = new FormData();
    body.set('key', key);
    body.set('value', value);
    await action('/dashboard/api/settings', { body, success: 'Setting saved' });
  }

  async function exportConfiguration() {
    const confirmed = window.confirm(
      'This configuration export contains provider API keys in plaintext. Download it only to a trusted device and store it securely. Continue?'
    );
    if (!confirmed) return;

    exporting = true;
    exportError = '';
    try {
      const response = await dashboardFetch('/dashboard/api/export', {
        cache: 'no-store',
        headers: { Accept: 'text/yaml, application/yaml, text/plain' }
      });
      if (!response.ok) throw new Error(await responseError(response));
      const contentType = response.headers.get('content-type') ?? '';
      if (contentType.includes('text/html')) {
        throw new Error(
          'The server returned an unexpected HTML response instead of a configuration export.'
        );
      }
      const blob = await response.blob();
      if (!blob.size) throw new Error('The configuration export was empty.');
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = 'janus-config.yaml';
      link.rel = 'noopener';
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 0);
    } catch (error) {
      exportError = error instanceof Error ? error.message : 'The configuration export failed.';
    } finally {
      exporting = false;
    }
  }
</script>

<PageHeader
  title="Settings"
  description="Control gateway behavior, observability, and dashboard access."
>
  <button class="button danger" disabled={exporting} on:click={exportConfiguration}>
    <Icon name="download" />{exporting ? 'Preparing export…' : 'Export secrets'}
  </button>
</PageHeader>

<section class="secret-export-warning" role="note">
  <Icon name="warning" size={18} />
  <div>
    <strong>Sensitive configuration export</strong>
    <p>
      The exported YAML includes provider API keys in plaintext. Janus fetches it without browser
      caching and only after confirmation.
    </p>
    {#if exportError}<p class="export-error" role="alert">{exportError}</p>{/if}
  </div>
</section>

<div class="panel-grid">
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Runtime behavior</h2>
        <p>Changes apply without restarting Janus</p>
      </div>
    </div>
    <div class="panel-body metric-list">
      {#each toggles as setting}
        <label
          class="item-card"
          style="display:flex;align-items:center;justify-content:space-between;gap:16px"
        >
          <span>
            <strong>{setting[1]}</strong>
            <small class="muted" style="display:block;margin-top:4px">{setting[0]}</small>
          </span>
          <input
            type="checkbox"
            checked={bool(settings[setting[0]] ?? data[setting[0]])}
            on:change={(event) =>
              save(
                setting[0],
                (event.currentTarget as HTMLInputElement).checked ? 'true' : 'false'
              )}
          />
        </label>
      {/each}
      <label class="field">
        <span>Reporting timezone</span>
        <input
          value={text(settings.server_reporting_timezone ?? data.reporting_timezone, 'UTC')}
          on:change={(event) =>
            save('server_reporting_timezone', (event.currentTarget as HTMLInputElement).value)}
        />
      </label>
      <label class="field">
        <span>Request log retention</span>
        <input
          type="number"
          min="50"
          max="5000"
          value={text(settings.server_request_log_retention ?? data.request_log_retention, '500')}
          on:change={(event) =>
            save('server_request_log_retention', (event.currentTarget as HTMLInputElement).value)}
        />
      </label>
      <label class="field">
        <span>Account strategy</span>
        <select
          value={text(settings.server_account_strategy ?? data.account_strategy, 'fill_first')}
          on:change={(event) =>
            save('server_account_strategy', (event.currentTarget as HTMLSelectElement).value)}
        >
          <option value="fill_first">Fill first</option>
          <option value="round_robin">Round robin</option>
          <option value="sticky_rr">Sticky round robin</option>
        </select>
      </label>
    </div>
  </section>

  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Dashboard access</h2>
        <p>API-key authentication protects every dashboard route</p>
      </div>
    </div>
    <div class="panel-body metric-list">
      <div class="item-card">
        <div style="display:flex;align-items:flex-start;gap:12px">
          <Icon name="key" size={20} />
          <div>
            <strong>API keys only</strong>
            <p class="muted" style="margin:5px 0 0;line-height:1.55">
              Sign in with an active Janus API key that has dashboard access enabled. This also
              applies on localhost; username and password sign-in is not supported.
            </p>
          </div>
        </div>
      </div>
      <div class="form-actions">
        <button
          class="button primary"
          type="button"
          on:click={() => navigate('/dashboard/ui/keys')}
        >
          <Icon name="key" size={15} />Manage API keys
        </button>
      </div>
    </div>
  </section>
</div>

<style>
  .secret-export-warning {
    display: flex;
    align-items: flex-start;
    gap: 11px;
    padding: 14px 16px;
    margin: -10px 0 18px;
    border: 1px solid color-mix(in srgb, var(--warning) 28%, var(--line));
    border-radius: 14px;
    color: var(--warning);
    background: color-mix(in srgb, var(--warning) 7%, var(--surface));
  }
  .secret-export-warning div {
    color: var(--text);
  }
  .secret-export-warning strong {
    font-size: 11px;
  }
  .secret-export-warning p {
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.5;
  }
  .secret-export-warning .export-error {
    color: var(--danger);
    font-weight: 650;
  }
</style>
