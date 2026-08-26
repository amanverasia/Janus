<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, csv, firstList, idOf, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  const apiTypes = [
    ['openai_compat', 'OpenAI compatible'],
    ['anthropic', 'Anthropic Messages'],
    ['gemini', 'Google Gemini'],
    ['github_copilot', 'GitHub Copilot'],
    ['codex', 'OpenAI Codex'],
    ['kiro', 'AWS Kiro'],
    ['cursor', 'Cursor'],
    ['antigravity', 'Google Antigravity'],
    ['gemini_cli', 'Gemini CLI'],
    ['gemini-cli', 'Gemini CLI (legacy ID)'],
    ['claude_oauth', 'Claude OAuth'],
    ['claude', 'Claude OAuth (legacy ID)'],
    ['opencode_free', 'OpenCode Free'],
    ['mimo_free', 'Xiaomi MiMo Free']
  ] as const;

  let open = false;
  let editing: JsonObject | undefined;
  let draft: JsonObject = {};
  let presetSearch = '';
  let selectedPresetId = '';
  let liveDiscovery = true;
  let modelText = '';
  let providerForm: HTMLFormElement;
  let fetchingModels = false;

  $: providers = firstList(data, 'providers', 'items');
  $: catalog = catalogRows(data.catalog_presets ?? data.catalog);
  $: filteredCatalog = catalog.filter((preset) => {
    const query = presetSearch.trim().toLowerCase();
    if (!query) return true;
    return [preset.id, preset.name, preset.prefix, preset.api_type]
      .map((value) => text(value, '').toLowerCase())
      .some((value) => value.includes(query));
  });

  function catalogRows(value: unknown): JsonObject[] {
    if (Array.isArray(value)) {
      return value.filter(
        (item): item is JsonObject =>
          item !== null && typeof item === 'object' && !Array.isArray(item)
      );
    }
    return Object.entries(object(value)).map(([id, entry]) => ({ id, ...object(entry) }));
  }

  function show(item?: JsonObject) {
    editing = item;
    draft = item ? { ...item } : {};
    selectedPresetId = text(item?.catalog_id, '');
    presetSearch = '';
    liveDiscovery = bool(item?.live_models, true);
    modelText = csv(item?.models_list ?? item?.models);
    open = true;
  }

  function choosePreset(preset: JsonObject) {
    const id = text(preset.id ?? preset.catalog_id, '');
    selectedPresetId = id;
    liveDiscovery = bool(preset.live_models, true);
    draft = {
      id,
      catalog_id: id,
      prefix: preset.prefix ?? id,
      api_type: preset.api_type ?? 'openai_compat',
      base_url: preset.base_url ?? '',
      models_list: preset.default_models ?? preset.models ?? [],
      default_model: preset.default_model ?? '',
      live_models: preset.live_models ?? true
    };
    modelText = csv(preset.default_models ?? preset.models);
  }

  function useCustomProvider() {
    selectedPresetId = 'custom';
    liveDiscovery = true;
    draft = {
      id: '',
      catalog_id: 'custom',
      prefix: '',
      api_type: 'openai_compat',
      base_url: '',
      models_list: [],
      default_model: '',
      live_models: true
    };
    modelText = '';
  }

  function providerModels(provider: JsonObject): string[] {
    return stringList(provider.models_list ?? provider.models);
  }

  function stringList(value: unknown): string[] {
    let decoded = value;
    if (typeof value === 'string') {
      try {
        decoded = JSON.parse(value);
      } catch {
        return [];
      }
    }
    return Array.isArray(decoded) ? decoded.map((model) => text(model, '')).filter(Boolean) : [];
  }

  function providerVisibleCount(provider: JsonObject, models: string[]): number {
    const selected = stringList(provider.selected_models);
    if (selected.includes('__janus_no_models_selected__')) return 0;
    return selected.length || models.length;
  }

  function catalogName(provider: JsonObject): string {
    const catalogId = text(provider.catalog_id, '');
    const preset = catalog.find((item) => text(item.id ?? item.catalog_id, '') === catalogId);
    return preset ? text(preset.name ?? preset.id) : catalogId || 'Custom provider';
  }

  async function submit(event: SubmitEvent) {
    const form = event.currentTarget as HTMLFormElement;
    const id = editing ? idOf(editing) : '';
    try {
      await action(
        id ? `/dashboard/api/providers/${encodeURIComponent(id)}` : '/dashboard/api/providers',
        {
          method: id ? 'PUT' : 'POST',
          body: new FormData(form),
          success: id ? 'Provider updated' : 'Provider connected'
        }
      );
    } catch {
      return;
    }
    open = false;
  }

  async function testProvider(provider: JsonObject) {
    try {
      await action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}/test`, {
        success: 'Connection test completed',
        refresh: false
      });
    } catch {
      return;
    }
  }

  async function fetchModels() {
    if (!providerForm) return;
    fetchingModels = true;
    const body = new FormData(providerForm);
    if (editing) body.set('provider_id', idOf(editing));
    try {
      const result = object(
        await action('/dashboard/api/providers/fetch-models', {
          body,
          success: 'Provider models fetched',
          refresh: false
        })
      );
      const models = firstList(result, 'models')
        .map((model) => text(model, ''))
        .filter(Boolean);
      modelText = models.join(', ');
    } catch {
      return;
    } finally {
      fetchingModels = false;
    }
  }
</script>

<PageHeader
  title="Providers"
  description="Connect upstream providers, discover their catalogs, and choose the models Janus can route."
>
  <button class="button primary" on:click={() => show()}><Icon name="plus" />Add provider</button>
</PageHeader>

{#if providers.length}
  <div class="provider-grid">
    {#each providers as provider}
      {@const models = providerModels(provider)}
      {@const visibleCount = providerVisibleCount(provider, models)}
      <article class="provider-card">
        <header>
          <div class="provider-identity">
            <span class="provider-mark">{text(provider.name ?? provider.id, '?').slice(0, 1)}</span>
            <div>
              <h3>{text(provider.name ?? provider.id)}</h3>
              <p class="mono">{text(provider.prefix)}/{text(provider.api_type)}</p>
            </div>
          </div>
          <span class="status {bool(provider.is_enabled, true) ? 'active' : 'disabled'}">
            {bool(provider.is_enabled, true) ? 'Ready' : 'Disabled'}
          </span>
        </header>

        <div class="provider-summary">
          <div>
            <span>Catalog</span>
            <strong>{catalogName(provider)}</strong>
          </div>
          <div>
            <span>Models</span>
            <strong>
              {visibleCount}{models.length ? ` / ${models.length}` : ''}
            </strong>
          </div>
          <div>
            <span>Discovery</span>
            <strong>{bool(provider.live_models, true) ? 'Live' : 'Configured'}</strong>
          </div>
        </div>

        <div class="provider-detail">
          <span>Default model</span>
          <code>{text(provider.default_model, 'Provider default')}</code>
        </div>
        <p class="provider-endpoint">{text(provider.base_url, 'Default upstream endpoint')}</p>

        <div class="card-actions">
          <button class="button" on:click={() => show(provider)}>
            <Icon name="edit" size={14} />Edit
          </button>
          <button class="button" on:click={() => testProvider(provider)}>Test</button>
          <button
            class="icon-button"
            title={bool(provider.is_enabled, true) ? 'Disable provider' : 'Enable provider'}
            aria-label={bool(provider.is_enabled, true) ? 'Disable provider' : 'Enable provider'}
            on:click={() =>
              action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}/toggle`, {
                method: 'PATCH',
                success: 'Provider status changed'
              })}
          >
            <Icon name="pulse" size={15} />
          </button>
          <button
            class="icon-button"
            title="Delete provider"
            aria-label="Delete provider"
            on:click={() =>
              confirm('Delete this provider?') &&
              action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}`, {
                method: 'DELETE',
                success: 'Provider deleted'
              })}
          >
            <Icon name="trash" size={15} />
          </button>
        </div>
      </article>
    {/each}
  </div>
{:else}
  <section class="panel">
    <EmptyState
      icon="plug"
      title="Connect your first provider"
      message="Choose a catalog preset or connect any compatible upstream."
    >
      <button class="button primary" on:click={() => show()}>Add provider</button>
    </EmptyState>
  </section>
{/if}

<Modal
  {open}
  title={editing ? 'Edit provider' : selectedPresetId ? 'Configure provider' : 'Choose a provider'}
  description="Credentials are write-only and are never returned in dashboard state."
  wide
  on:close={() => (open = false)}
>
  {#if !editing && !selectedPresetId}
    <div class="preset-toolbar">
      <Icon name="search" size={16} />
      <input bind:value={presetSearch} type="search" placeholder="Search provider presets…" />
    </div>
    <div class="preset-grid">
      {#each filteredCatalog as preset}
        <button type="button" class="preset-card" on:click={() => choosePreset(preset)}>
          <span class="provider-mark">{text(preset.name ?? preset.id, '?').slice(0, 1)}</span>
          <span>
            <strong>{text(preset.name ?? preset.id)}</strong>
            <small>{text(preset.api_type, 'OpenAI compatible')} · {text(preset.prefix)}</small>
          </span>
          <Icon name="arrow" size={15} />
        </button>
      {/each}
      <button type="button" class="preset-card custom" on:click={useCustomProvider}>
        <span class="provider-mark"><Icon name="plus" size={16} /></span>
        <span>
          <strong>Custom provider</strong>
          <small>Bring any supported compatible endpoint</small>
        </span>
        <Icon name="arrow" size={15} />
      </button>
    </div>
    {#if !filteredCatalog.length}
      <EmptyState icon="plug" title="No provider presets found" message="Try another search." />
    {/if}
  {:else}
    <form bind:this={providerForm} on:submit|preventDefault={submit}>
      <input type="hidden" name="catalog_id" value={text(draft.catalog_id, '')} />
      <input type="hidden" name="live_models" value={liveDiscovery ? 'true' : 'false'} />
      <div class="field-grid">
        <label class="field">
          <span>Provider ID</span>
          <input name="id" required disabled={!!editing} value={text(draft.id, '')} />
        </label>
        <label class="field">
          <span>Prefix</span>
          <input name="prefix" required value={text(draft.prefix, '')} />
        </label>
        <label class="field">
          <span>API type</span>
          <select name="api_type" value={text(draft.api_type, 'openai_compat')}>
            {#each apiTypes as apiType}<option value={apiType[0]}>{apiType[1]}</option>{/each}
          </select>
        </label>
        <label class="field">
          <span>Base URL</span>
          <input
            name="base_url"
            type="url"
            value={text(draft.base_url, '')}
            placeholder="https://api.example.com"
          />
        </label>
        <label class="field full">
          <span>API key</span>
          <input
            name="api_key"
            type="password"
            autocomplete="new-password"
            placeholder={editing ? 'Leave blank to preserve the configured key' : 'sk-…'}
          />
          <small>The configured credential is never rendered back into this form.</small>
        </label>
        <div class="field full">
          <div class="field-title">
            <span>Models</span>
            <button
              type="button"
              class="button ghost compact"
              disabled={fetchingModels}
              on:click={fetchModels}
            >
              <Icon name="refresh" size={13} />
              {fetchingModels ? 'Fetching…' : 'Fetch models'}
            </button>
          </div>
          <textarea
            name="models"
            required={!liveDiscovery}
            bind:value={modelText}
            placeholder="model-one, model-two"></textarea>
          <small>Seed models used when live discovery is unavailable or disabled.</small>
        </div>
        <label class="field">
          <span>Default model</span>
          <input
            name="default_model"
            value={text(draft.default_model, '')}
            placeholder="Optional provider default"
          />
        </label>
        <label class="field">
          <span>Allowed models</span>
          <input
            name="allowed_models"
            value={csv(draft.allowed_models_list)}
            placeholder="Optional allowlist"
          />
        </label>
        <label class="check-field full">
          <input type="checkbox" bind:checked={liveDiscovery} />
          <span>
            <strong>Discover models from provider</strong>
            <br />
            Fetch the upstream catalog and merge it with configured custom models.
          </span>
        </label>
        <label class="field">
          <span>Quota window</span>
          <select name="quota_window" value={text(draft.quota_window, '')}>
            <option value="">No quota</option>
            <option value="5h">5 hours</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </select>
        </label>
        <label class="field">
          <span>Quota limit</span>
          <input name="quota_limit" type="number" min="1" value={text(draft.quota_limit, '')} />
        </label>
        <label class="field">
          <span>Quota metric</span>
          <select name="quota_metric" value={text(draft.quota_metric, 'requests')}>
            <option value="requests">Requests</option>
            <option value="tokens">Tokens</option>
          </select>
        </label>
      </div>
      <div class="form-actions split-actions">
        {#if !editing}
          <button type="button" class="button ghost" on:click={() => (selectedPresetId = '')}>
            Back to presets
          </button>
        {/if}
        <span></span>
        <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
        <button class="button primary">{editing ? 'Save changes' : 'Connect provider'}</button>
      </div>
    </form>
  {/if}
</Modal>
