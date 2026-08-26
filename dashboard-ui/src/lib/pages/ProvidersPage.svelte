<script lang="ts">
  import { onDestroy } from 'svelte';
  import { dashboardFetch, responseError } from '$lib/api';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, csv, firstList, idOf, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  type CatalogTab = 'accounts' | 'free' | 'paid';
  type DetailTab = 'overview' | 'models' | 'accounts' | 'limits';
  type ProviderGroup = {
    key: string;
    prefix: string;
    label: string;
    representative: JsonObject;
    rows: JsonObject[];
  };

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
  let providerSearch = '';
  let presetSearch = '';
  let selectedPresetId = '';
  let selectedProviderId = '';
  let catalogTab: CatalogTab = 'free';
  let detailTab: DetailTab = 'overview';
  let liveDiscovery = true;
  let modelText = '';
  let providerForm: HTMLFormElement;
  let apiKeyInput: HTMLInputElement | undefined;
  let fetchingModels = false;
  let copilotDeviceCode = '';
  let copilotUserCode = '';
  let copilotVerificationUri = '';
  let copilotExpiresAt = 0;
  let copilotPollInterval = 5000;
  let copilotTimer: number | undefined;
  let copilotGeneration = 0;
  let copilotStarting = false;
  let copilotPolling = false;
  let copilotStatus = '';
  let copilotError = '';

  $: providers = firstList(data, 'providers', 'items');
  $: catalog = catalogRows(data.catalog_presets ?? data.catalog);
  $: logoMap = object(data.logo_map);
  $: providerGroups = buildProviderGroups(providers);
  $: filteredProviderGroups = providerGroups.filter((group) => {
    const query = providerSearch.trim().toLowerCase();
    if (!query) return true;
    return [
      group.label,
      group.prefix,
      ...group.rows.flatMap((provider) => [provider.id, provider.api_type])
    ]
      .map((value) => text(value, '').toLowerCase())
      .some((value) => value.includes(query));
  });
  $: readyProviderGroups = filteredProviderGroups.filter(
    (group) => providerGroupStatus(group) === 'Ready'
  );
  $: setupProviderGroups = filteredProviderGroups.filter(
    (group) => providerGroupStatus(group) === 'Needs setup'
  );
  $: disabledProviderGroups = filteredProviderGroups.filter(
    (group) => providerGroupStatus(group) === 'Disabled'
  );
  $: selectedProvider = providers.find((provider) => idOf(provider) === selectedProviderId);
  $: selectedPrefixProviders = selectedProvider
    ? providers.filter(
        (provider) => text(provider.prefix, '') === text(selectedProvider?.prefix, '')
      )
    : [];
  $: filteredCatalog = catalog
    .filter((preset) => text(preset.id ?? preset.catalog_id, '') !== 'custom')
    .filter((preset) => presetTier(preset) === catalogTab)
    .filter((preset) => {
      const query = presetSearch.trim().toLowerCase();
      if (!query) return true;
      return [preset.id, preset.name, preset.prefix]
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

  function providerModels(provider: JsonObject): string[] {
    return stringList(provider.models_list ?? provider.models);
  }

  function buildProviderGroups(rows: JsonObject[]): ProviderGroup[] {
    const groups = new Map<string, ProviderGroup>();
    for (const provider of rows) {
      const prefix = text(provider.prefix, idOf(provider));
      const key = prefix || `connection:${idOf(provider)}`;
      const existing = groups.get(key);
      if (existing) {
        existing.rows.push(provider);
        if (!bool(existing.representative.is_enabled, true) && bool(provider.is_enabled, true)) {
          existing.representative = provider;
          existing.label = providerDisplayName(provider);
        }
        continue;
      }
      groups.set(key, {
        key,
        prefix,
        label: providerDisplayName(provider),
        representative: provider,
        rows: [provider]
      });
    }
    return [...groups.values()].sort((left, right) => left.label.localeCompare(right.label));
  }

  function count(value: unknown, fallback = 0): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
  }

  function providerModelCount(provider: JsonObject): number {
    return count(provider.catalog_model_count, providerModels(provider).length);
  }

  function providerVisibleCount(provider: JsonObject): number {
    if (provider.visible_model_count !== undefined) return count(provider.visible_model_count);
    const models = providerModels(provider);
    const selected = stringList(provider.selected_models);
    if (selected.includes('__janus_no_models_selected__')) return 0;
    return selected.length || models.length;
  }

  function providerAccountCount(provider: JsonObject): number {
    return count(provider.account_count, count(object(provider.inventory_keys).total));
  }

  function presetForProvider(provider: JsonObject): JsonObject | undefined {
    const catalogId = text(provider.catalog_id, '');
    return catalog.find((item) => text(item.id ?? item.catalog_id, '') === catalogId);
  }

  function providerDisplayName(provider: JsonObject): string {
    const preset = presetForProvider(provider);
    return text(provider.name ?? preset?.name ?? provider.id, 'Unnamed provider');
  }

  function catalogName(provider: JsonObject): string {
    const preset = presetForProvider(provider);
    return preset ? text(preset.name ?? preset.id) : text(provider.catalog_id, 'Custom provider');
  }

  function authKind(value: JsonObject): string {
    const preset = value.catalog_id !== undefined ? presetForProvider(value) : value;
    return text(preset?.auth_kind, text(value.api_type, '') === 'openai_compat' ? 'key' : 'oauth');
  }

  function keyOptional(value: JsonObject): boolean {
    const preset = value.catalog_id !== undefined ? presetForProvider(value) : value;
    return bool(preset?.key_optional) || authKind(value) === 'local';
  }

  function providerHasCredential(provider: JsonObject): boolean {
    return bool(provider.has_api_key) || providerAccountCount(provider) > 0;
  }

  function providerNeedsSetup(provider: JsonObject): boolean {
    return !keyOptional(provider) && !providerHasCredential(provider);
  }

  function providerStatus(provider: JsonObject): 'Ready' | 'Needs setup' | 'Disabled' {
    if (!bool(provider.is_enabled, true)) return 'Disabled';
    return providerNeedsSetup(provider) ? 'Needs setup' : 'Ready';
  }

  function providerStatusClass(provider: JsonObject): string {
    const status = providerStatus(provider);
    return status === 'Ready' ? 'active' : status === 'Needs setup' ? 'warning' : 'disabled';
  }

  function providerGroupStatus(group: ProviderGroup): 'Ready' | 'Needs setup' | 'Disabled' {
    if (group.rows.some((provider) => providerStatus(provider) === 'Ready')) return 'Ready';
    if (group.rows.some((provider) => providerStatus(provider) === 'Needs setup')) {
      return 'Needs setup';
    }
    return 'Disabled';
  }

  function providerGroupStatusClass(group: ProviderGroup): string {
    const status = providerGroupStatus(group);
    return status === 'Ready' ? 'active' : status === 'Needs setup' ? 'warning' : 'disabled';
  }

  function presetTier(preset: JsonObject): CatalogTab {
    const group = text(preset.group, '');
    if (group === 'accounts' || text(preset.auth_kind, '') === 'oauth') return 'accounts';
    if (group === 'free' || group === 'local' || bool(preset.key_optional)) return 'free';
    return 'paid';
  }

  function logoFilename(value: JsonObject): string {
    const preset = value.catalog_id !== undefined ? presetForProvider(value) : value;
    const keys = [value.catalog_id, value.prefix, value.id, preset?.id, preset?.prefix];
    for (const key of keys) {
      const filename = text(logoMap[text(key, '')], '');
      if (filename) return filename;
    }
    return text(preset?.logo, '');
  }

  function logoUrl(value: JsonObject): string {
    const filename = logoFilename(value);
    return filename ? `/dashboard/static/logos/${encodeURIComponent(filename)}` : '';
  }

  function displayInitial(value: JsonObject): string {
    return text(value.name ?? value.id ?? value.prefix, '?')
      .trim()
      .slice(0, 1)
      .toUpperCase();
  }

  function authLabel(value: JsonObject): string {
    const kind = authKind(value);
    if (kind === 'local') return 'Local';
    if (kind === 'oauth') {
      return text(value.api_type, '') === 'github_copilot'
        ? 'Device OAuth'
        : 'Inventory credential';
    }
    return keyOptional(value) ? 'No key required' : 'API key';
  }

  function credentialInstructions(value: JsonObject): string {
    const preset = value.catalog_id !== undefined ? presetForProvider(value) : value;
    const catalogInstructions = text(
      preset?.credential_instructions ?? preset?.inventory_routing_note ?? preset?.routing_note,
      ''
    );
    if (catalogInstructions && catalogInstructions !== 'Provider preset')
      return catalogInstructions;

    switch (text(value.api_type ?? preset?.api_type, '')) {
      case 'codex':
        return 'Add a Janus OAuth JSON object, a 9router Codex connection object or providerConnections export, or a bare access token.';
      case 'kiro':
        return 'Add a Kiro credential JSON object containing accessToken and refreshToken.';
      case 'antigravity':
        return 'Add an Antigravity OAuth JSON object containing access_token/accessToken and refresh_token/refreshToken. Project metadata may be included as projectId.';
      case 'claude':
      case 'claude_oauth':
        return 'Add a Claude credential JSON object containing access_token and refresh_token, or a bare access token.';
      case 'cursor':
        return 'Add the bearer credential used by your Cursor account or compatible Cursor bridge. Janus does not generate this credential.';
      default:
        return 'Add the exported credential or token expected by this provider.';
    }
  }

  function providerCredentialSummary(provider: JsonObject): string {
    const inventoryCount = providerAccountCount(provider);
    if (keyOptional(provider)) return 'No credential required';
    if (inventoryCount) {
      return `${inventoryCount} Inventory credential${inventoryCount === 1 ? '' : 's'} configured`;
    }
    if (bool(provider.has_api_key)) return 'Direct bootstrap credential configured';
    return 'Add credentials in Inventory';
  }

  function capabilityLabels(preset: JsonObject): string[] {
    return Object.entries(object(preset.capabilities))
      .filter(([, enabled]) => bool(enabled))
      .map(([name]) => name.replaceAll('_', ' '));
  }

  function show(item?: JsonObject) {
    stopCopilotFlow();
    editing = item;
    draft = item ? { ...item } : {};
    selectedPresetId = text(item?.catalog_id, '');
    presetSearch = '';
    catalogTab = 'free';
    liveDiscovery = bool(item?.live_models, true);
    modelText = csv(item?.models_list ?? item?.models);
    open = true;
  }

  function choosePreset(preset: JsonObject) {
    stopCopilotFlow();
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
    stopCopilotFlow();
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

  function selectProvider(provider: JsonObject) {
    selectedProviderId = idOf(provider);
    detailTab = 'overview';
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
          success: id ? 'Routing connection updated' : 'Routing connection created'
        }
      );
    } catch {
      return;
    }
    closeModal();
    if (id) selectedProviderId = id;
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

  async function toggleProvider(provider: JsonObject) {
    try {
      await action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}/toggle`, {
        method: 'PATCH',
        success: 'Provider status changed'
      });
    } catch {
      return;
    }
  }

  async function removeProvider(provider: JsonObject) {
    const providerId = idOf(provider);
    if (
      !confirm(
        `Delete connection ${providerId} (${providerDisplayName(provider)})? This removes this exact gateway configuration.`
      )
    )
      return;
    try {
      await action(`/dashboard/api/providers/${encodeURIComponent(providerId)}`, {
        method: 'DELETE',
        success: `Connection ${providerId} deleted`
      });
    } catch {
      return;
    }
    if (selectedProviderId === providerId) selectedProviderId = '';
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

  function safeGitHubVerificationUri(value: unknown): string {
    try {
      const url = new URL(text(value, ''));
      if (url.protocol === 'https:' && url.hostname === 'github.com') return url.toString();
    } catch {
      return 'https://github.com/login/device';
    }
    return 'https://github.com/login/device';
  }

  function stopCopilotFlow(clearState = true) {
    copilotGeneration += 1;
    if (copilotTimer !== undefined) window.clearTimeout(copilotTimer);
    copilotTimer = undefined;
    copilotStarting = false;
    copilotPolling = false;
    copilotDeviceCode = '';
    if (!clearState) return;
    if (apiKeyInput) apiKeyInput.value = '';
    copilotUserCode = '';
    copilotVerificationUri = '';
    copilotExpiresAt = 0;
    copilotStatus = '';
    copilotError = '';
  }

  function closeModal() {
    stopCopilotFlow();
    open = false;
  }

  function backToCatalog() {
    stopCopilotFlow();
    selectedPresetId = '';
  }

  function scheduleCopilotPoll(generation: number, delay = copilotPollInterval) {
    if (!open || !copilotDeviceCode || generation !== copilotGeneration) return;
    copilotTimer = window.setTimeout(() => void pollCopilotOAuth(generation), delay);
  }

  async function startCopilotOAuth() {
    stopCopilotFlow();
    const generation = copilotGeneration;
    copilotStarting = true;
    copilotStatus = 'Starting secure GitHub device authorization…';
    try {
      const response = await dashboardFetch('/dashboard/api/oauth/copilot/start', {
        method: 'POST',
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = object(await response.json());
      if (generation !== copilotGeneration || !open) return;
      const deviceCode = text(result.device_code, '');
      const userCode = text(result.user_code, '');
      if (!deviceCode || !userCode) throw new Error('GitHub did not return a device code.');
      const interval = Number(result.interval);
      const expiresIn = Number(result.expires_in);
      copilotDeviceCode = deviceCode;
      copilotUserCode = userCode;
      copilotVerificationUri = safeGitHubVerificationUri(result.verification_uri);
      copilotPollInterval =
        Number.isFinite(interval) && interval > 0 ? Math.max(interval * 1000, 1000) : 5000;
      copilotExpiresAt =
        Date.now() + (Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn * 1000 : 900_000);
      copilotStatus = 'Waiting for authorization on GitHub…';
      scheduleCopilotPoll(generation);
    } catch (caught) {
      if (generation !== copilotGeneration) return;
      copilotStatus = '';
      copilotError =
        caught instanceof Error ? caught.message : 'GitHub authorization could not start.';
    } finally {
      if (generation === copilotGeneration) copilotStarting = false;
    }
  }

  async function pollCopilotOAuth(generation: number) {
    if (generation !== copilotGeneration || !open || !copilotDeviceCode) return;
    if (Date.now() >= copilotExpiresAt) {
      copilotError = 'This GitHub device code expired. Start a new authorization to continue.';
      copilotStatus = '';
      stopCopilotFlow(false);
      return;
    }
    copilotPolling = true;
    try {
      const body = new URLSearchParams({ device_code: copilotDeviceCode });
      const response = await dashboardFetch('/dashboard/api/oauth/copilot/poll', {
        method: 'POST',
        body,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
        }
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = object(await response.json());
      if (generation !== copilotGeneration || !open) return;
      const status = text(result.status, 'error');
      if (status === 'success') {
        const token = text(result.access_token, '');
        if (!token) throw new Error('GitHub completed authorization without returning a token.');
        if (!apiKeyInput) throw new Error('The provider credential field is no longer available.');
        apiKeyInput.value = token;
        copilotStatus = 'GitHub authorized. The token is ready to save with this provider.';
        copilotError = '';
        stopCopilotFlow(false);
        return;
      }
      if (status === 'pending') {
        copilotStatus = 'Waiting for authorization on GitHub…';
        if (text(result.error, '') === 'slow_down') copilotPollInterval += 5000;
        scheduleCopilotPoll(generation);
        return;
      }
      const error = text(result.error, 'authorization_failed').replaceAll('_', ' ');
      copilotError = `GitHub authorization failed: ${error}.`;
      copilotStatus = '';
      stopCopilotFlow(false);
    } catch (caught) {
      if (generation !== copilotGeneration) return;
      copilotError = caught instanceof Error ? caught.message : 'GitHub authorization failed.';
      copilotStatus = '';
      stopCopilotFlow(false);
    } finally {
      if (generation === copilotGeneration) copilotPolling = false;
    }
  }

  onDestroy(() => stopCopilotFlow());
</script>

<PageHeader
  title="Providers"
  description="Configure routing connections, model discovery, and shared Inventory credential pools."
>
  <button class="button primary" on:click={() => show()}><Icon name="plus" />Add provider</button>
</PageHeader>

<div class="provider-workspace">
  <aside class="provider-rail" aria-label="Provider list">
    <label class="workspace-search">
      <Icon name="search" size={16} />
      <span class="sr-only">Search providers</span>
      <input bind:value={providerSearch} type="search" placeholder="Search providers…" />
    </label>

    <button
      class:active={!selectedProvider}
      class="provider-overview-link"
      type="button"
      aria-pressed={!selectedProvider}
      on:click={() => (selectedProviderId = '')}
    >
      <span class="provider-mark"><Icon name="layers" size={16} /></span>
      <span>
        <strong>Provider overview</strong>
        <small>{providerGroups.length} providers · {providers.length} connections</small>
      </span>
      <Icon name="arrow" size={14} />
    </button>

    <div class="provider-rail-list" aria-label="Providers">
      {#each [{ label: 'Ready', rows: readyProviderGroups }, { label: 'Needs setup', rows: setupProviderGroups }, { label: 'Disabled', rows: disabledProviderGroups }] as section}
        {#if section.rows.length}
          <section
            class="provider-rail-group"
            aria-label={`${section.label} (${section.rows.length})`}
          >
            <header>
              <span>{section.label}</span>
              <span>{section.rows.length}</span>
            </header>
            {#each section.rows as group}
              {@const provider = group.representative}
              {@const providerLogo = logoUrl(provider)}
              <button
                type="button"
                aria-pressed={text(selectedProvider?.prefix, '') === group.prefix}
                class:selected={text(selectedProvider?.prefix, '') === group.prefix}
                class="provider-rail-row"
                on:click={() => selectProvider(provider)}
              >
                <span class="provider-mark">
                  {#if providerLogo}
                    <img
                      src={providerLogo}
                      alt=""
                      on:error={(event) =>
                        ((event.currentTarget as HTMLImageElement).hidden = true)}
                    />
                  {:else}
                    {displayInitial(provider)}
                  {/if}
                </span>
                <span class="provider-rail-copy">
                  <strong>{group.label}</strong>
                  <small>
                    {idOf(provider)} · {group.prefix}{group.rows.length > 1
                      ? ` · ${group.rows.length} connections`
                      : ''}
                  </small>
                </span>
                <span
                  class="provider-status-dot {providerGroupStatusClass(group)}"
                  title={providerGroupStatus(group)}
                ></span>
              </button>
            {/each}
          </section>
        {/if}
      {/each}
      {#if !filteredProviderGroups.length}
        <p class="workspace-empty-copy">
          {providerGroups.length
            ? 'No providers match this search.'
            : 'No providers configured yet.'}
        </p>
      {/if}
    </div>
  </aside>

  <main class="provider-workspace-detail" aria-label="Provider details">
    {#if selectedProvider}
      {@const selectedModels = providerModels(selectedProvider)}
      {@const selectedLogo = logoUrl(selectedProvider)}
      <button class="workspace-back" type="button" on:click={() => (selectedProviderId = '')}>
        <Icon name="arrow" size={13} />Provider overview
      </button>
      <header class="provider-detail-head">
        <div class="provider-detail-identity">
          <span class="provider-mark large">
            {#if selectedLogo}
              <img
                src={selectedLogo}
                alt=""
                on:error={(event) => ((event.currentTarget as HTMLImageElement).hidden = true)}
              />
            {:else}
              {displayInitial(selectedProvider)}
            {/if}
          </span>
          <div>
            <h2>{providerDisplayName(selectedProvider)}</h2>
            <p>
              Connection <code>{idOf(selectedProvider)}</code>
              · prefix
              <code>{text(selectedProvider.prefix)}</code>
              · {catalogName(selectedProvider)}
            </p>
          </div>
        </div>
        <div class="provider-detail-controls">
          <span class="status {providerStatusClass(selectedProvider)}">
            {providerStatus(selectedProvider)}
          </span>
          <button
            class="button"
            aria-pressed={bool(selectedProvider.is_enabled, true)}
            on:click={() => toggleProvider(selectedProvider)}
          >
            {bool(selectedProvider.is_enabled, true) ? 'Enabled' : 'Disabled'}
          </button>
          <button
            class="icon-button"
            aria-label={`Delete connection ${idOf(selectedProvider)}`}
            on:click={() => removeProvider(selectedProvider)}
          >
            <Icon name="trash" size={15} />
          </button>
        </div>
      </header>

      <div class="workspace-tabs" aria-label="Provider details">
        {#each [['overview', 'Overview'], ['models', 'Models & routing'], ['accounts', 'Accounts / API keys'], ['limits', 'Limits']] as tab}
          <button
            type="button"
            aria-pressed={detailTab === tab[0]}
            class:active={detailTab === tab[0]}
            on:click={() => (detailTab = tab[0] as DetailTab)}
          >
            {tab[1]}
          </button>
        {/each}
      </div>

      {#if detailTab === 'overview'}
        <div class="provider-detail-grid">
          <section class="workspace-section">
            <header>
              <h3>Connection</h3>
              <button class="button ghost compact" on:click={() => show(selectedProvider)}>
                Edit settings
              </button>
            </header>
            <dl class="workspace-definition-list">
              <div>
                <dt>Status</dt>
                <dd>
                  <span class="status {providerStatusClass(selectedProvider)}">
                    {providerStatus(selectedProvider)}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Base URL</dt>
                <dd><code>{text(selectedProvider.base_url, 'Provider default')}</code></dd>
              </div>
              <div>
                <dt>Authentication</dt>
                <dd>{authLabel(selectedProvider)}</dd>
              </div>
              <div>
                <dt>Default model</dt>
                <dd><code>{text(selectedProvider.default_model, 'Provider default')}</code></dd>
              </div>
            </dl>
          </section>
          <aside class="workspace-section provider-auth-card">
            <span class="workspace-section-label">Authentication</span>
            <strong>{providerCredentialSummary(selectedProvider)}</strong>
            <p>
              Keep one routing connection for this prefix and manage the shared credential pool in
              Inventory. Secrets are write-only.
            </p>
            <button class="button" on:click={() => testProvider(selectedProvider)}>
              Test connection
            </button>
          </aside>
        </div>
      {:else if detailTab === 'models'}
        <section class="workspace-section">
          <header>
            <div>
              <h3>Models</h3>
              <p>
                {providerVisibleCount(selectedProvider)}/{providerModelCount(selectedProvider)} visible
                in the shared catalog
              </p>
            </div>
            <button class="button" on:click={() => show(selectedProvider)}>
              Configure discovery
            </button>
          </header>
          <div class="provider-model-summary">
            <div>
              <span>Discovery</span>
              <strong>
                {bool(selectedProvider.live_models, true) ? 'Live' : 'Configured only'}
              </strong>
            </div>
            <div>
              <span>Routing prefix</span>
              <strong><code>{text(selectedProvider.prefix)}</code></strong>
            </div>
            <div>
              <span>Default</span>
              <strong><code>{text(selectedProvider.default_model, 'Automatic')}</code></strong>
            </div>
          </div>
          {#if selectedModels.length}
            <div class="provider-model-preview">
              {#each selectedModels.slice(0, 12) as model}<code>{model}</code>{/each}
              {#if selectedModels.length > 12}<span>+{selectedModels.length - 12} more</span>{/if}
            </div>
            <div class="provider-model-catalog-note">
              <p>
                {providerModelCount(selectedProvider) > selectedModels.length
                  ? `The shared catalog contains ${providerModelCount(selectedProvider)} models; this preview shows ${selectedModels.length} configured seed models.`
                  : `${providerModelCount(selectedProvider)} models are currently available in the shared catalog.`}
              </p>
              <a
                class="button ghost compact"
                href={`/dashboard/ui/models?provider=${encodeURIComponent(text(selectedProvider.prefix, ''))}`}
              >
                Manage models
              </a>
            </div>
          {:else if providerModelCount(selectedProvider) > 0}
            <div class="workspace-inline-empty">
              <Icon name="layers" size={18} />
              <div>
                <strong>
                  {providerModelCount(selectedProvider)} models discovered in the shared catalog
                </strong>
                <p>
                  This connection has no static seed list. Live discovery and custom models are
                  available on the Models page.
                </p>
              </div>
              <a
                class="button"
                href={`/dashboard/ui/models?provider=${encodeURIComponent(text(selectedProvider.prefix, ''))}`}
              >
                Manage models
              </a>
            </div>
          {:else}
            <div class="workspace-inline-empty">
              <Icon name="layers" size={18} />
              <div>
                <strong>No models in the shared catalog yet</strong>
                <p>Fetch the provider catalog, add seed models, or add a custom model.</p>
              </div>
              <a
                class="button"
                href={`/dashboard/ui/models?provider=${encodeURIComponent(text(selectedProvider.prefix, ''))}`}
              >
                Open Models
              </a>
            </div>
          {/if}
        </section>
      {:else if detailTab === 'accounts'}
        {@const inventory = object(selectedProvider.inventory_keys)}
        <section class="workspace-section">
          <header>
            <div>
              <h3>Accounts and API keys</h3>
              <p>Janus routes across the shared credential pool for this provider.</p>
            </div>
            <div class="provider-account-links">
              <a
                class="button"
                href={`/dashboard/ui/inventory/keys?provider_id=${encodeURIComponent(text(selectedProvider.inventory_provider_id, ''))}`}
              >
                Manage credentials
              </a>
              <a class="button" href="/dashboard/ui/inventory/add">Add credentials</a>
              <a class="button ghost" href="/dashboard/ui/inventory/import">Import JSON</a>
            </div>
          </header>
          <div class="provider-model-summary">
            <div>
              <span>Total accounts</span>
              <strong>{count(inventory.total)}</strong>
            </div>
            <div>
              <span>Routable now</span>
              <strong>{count(inventory.routable)}</strong>
            </div>
            <div>
              <span>Pending validation</span>
              <strong>{count(inventory.pending)}</strong>
            </div>
          </div>
          <span class="workspace-section-label">
            Gateway configurations for prefix <code>{text(selectedProvider.prefix)}</code>
          </span>
          <div class="gateway-config-list" aria-label="Gateway configurations for this prefix">
            {#each selectedPrefixProviders as provider}
              <article class:selected={idOf(provider) === idOf(selectedProvider)}>
                <span class="provider-status-dot {providerStatusClass(provider)}"></span>
                <div>
                  <strong><code>{idOf(provider)}</code></strong>
                  <small>
                    Prefix <code>{text(provider.prefix)}</code>
                    · {providerStatus(provider)}
                  </small>
                  <code class="gateway-config-url">
                    {text(provider.base_url, 'Provider default endpoint')}
                  </code>
                </div>
                <div class="gateway-config-actions">
                  <button
                    type="button"
                    class="button ghost compact"
                    aria-label={`Edit connection ${idOf(provider)}`}
                    on:click={() => show(provider)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    class="icon-button"
                    aria-label={`Delete connection ${idOf(provider)}`}
                    on:click={() => removeProvider(provider)}
                  >
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              </article>
            {/each}
          </div>
          <div class="workspace-inline-empty account-pool-note">
            <Icon name="layers" size={18} />
            <div>
              <strong>Shared multi-account routing</strong>
              <p>
                Credentials are validated in Inventory and expanded into independent routing
                accounts with cooldowns, quota awareness, and fallback. Provider settings define the
                adapter and model namespace; credentials stay in the account pool.
              </p>
            </div>
          </div>
        </section>
      {:else}
        <section class="workspace-section">
          <header>
            <div>
              <h3>Limits</h3>
              <p>
                Soft provider quotas influence account ordering without blocking explicit routing.
              </p>
            </div>
            <button class="button" on:click={() => show(selectedProvider)}>Edit limits</button>
          </header>
          <dl class="workspace-definition-list">
            <div>
              <dt>Window</dt>
              <dd>{text(selectedProvider.quota_window, 'No quota')}</dd>
            </div>
            <div>
              <dt>Limit</dt>
              <dd>{text(selectedProvider.quota_limit, '—')}</dd>
            </div>
            <div>
              <dt>Metric</dt>
              <dd>{text(selectedProvider.quota_metric, 'Requests')}</dd>
            </div>
            <div>
              <dt>Current status</dt>
              <dd>{text(object(selectedProvider.quota).status, 'Not tracked')}</dd>
            </div>
          </dl>
        </section>
      {/if}
    {:else}
      <header class="workspace-overview-head">
        <div>
          <h2>Providers overview</h2>
          <p>
            Each provider is counted once by routing prefix. Exact gateway connections remain
            available in provider details.
          </p>
        </div>
      </header>
      <section class="workspace-summary" aria-label="Provider summary">
        <div class="ready">
          <strong>
            {providerGroups.filter((group) => providerGroupStatus(group) === 'Ready').length}
          </strong>
          <span>Ready</span>
        </div>
        <div class="warning">
          <strong>
            {providerGroups.filter((group) => providerGroupStatus(group) === 'Needs setup').length}
          </strong>
          <span>Needs setup</span>
        </div>
        <div>
          <strong>
            {providerGroups.filter((group) => providerGroupStatus(group) === 'Disabled').length}
          </strong>
          <span>Disabled</span>
        </div>
      </section>

      {#if providers.length}
        <div class="provider-overview-columns">
          <section class="workspace-section">
            <header><h3>Needs attention</h3></header>
            {#if providerGroups.some((group) => providerGroupStatus(group) === 'Needs setup')}
              <div class="workspace-row-list">
                {#each providerGroups.filter((group) => providerGroupStatus(group) === 'Needs setup') as group}
                  {@const provider = group.representative}
                  <button type="button" on:click={() => selectProvider(provider)}>
                    <span class="provider-mark">{displayInitial(provider)}</span>
                    <span>
                      <strong>{group.label}</strong>
                      <small>
                        Add Inventory credentials · prefix {group.prefix}{group.rows.length > 1
                          ? ` · ${group.rows.length} connections`
                          : ''}
                      </small>
                    </span>
                    <Icon name="arrow" size={14} />
                  </button>
                {/each}
              </div>
            {:else}
              <p class="workspace-empty-copy">All enabled providers are ready.</p>
            {/if}
          </section>
          <section class="workspace-section">
            <header><h3>Catalog coverage</h3></header>
            <div class="workspace-row-list">
              {#each providerGroups
                .slice()
                .sort((left, right) => providerModelCount(right.representative) - providerModelCount(left.representative))
                .slice(0, 6) as group}
                {@const provider = group.representative}
                <button type="button" on:click={() => selectProvider(provider)}>
                  <span class="provider-mark">{displayInitial(provider)}</span>
                  <span>
                    <strong>{group.label}</strong>
                    <small>
                      {providerVisibleCount(provider)}/{providerModelCount(provider)} visible ·
                      {providerAccountCount(provider)} Inventory credentials{group.rows.length > 1
                        ? ` · ${group.rows.length} connections`
                        : ''}
                    </small>
                  </span>
                  <Icon name="arrow" size={14} />
                </button>
              {/each}
            </div>
          </section>
        </div>
      {:else}
        <EmptyState
          icon="plug"
          title="Connect your first provider"
          message="Choose a catalog preset or connect any compatible upstream."
        >
          <button class="button primary" on:click={() => show()}>Add provider</button>
        </EmptyState>
      {/if}
    {/if}
  </main>
</div>

<Modal
  {open}
  title={editing ? 'Edit provider' : selectedPresetId ? 'Configure provider' : 'Add provider'}
  description="Credentials are write-only and are never returned in dashboard state."
  wide
  on:close={closeModal}
>
  {#if !editing && !selectedPresetId}
    <div class="provider-catalog-tabs" aria-label="Provider categories">
      {#each [['accounts', 'Accounts'], ['free', 'Free'], ['paid', 'Paid']] as tab}
        <button
          type="button"
          aria-pressed={catalogTab === tab[0]}
          class:active={catalogTab === tab[0]}
          on:click={() => (catalogTab = tab[0] as CatalogTab)}
        >
          {tab[1]}
        </button>
      {/each}
    </div>
    <label class="preset-toolbar">
      <Icon name="search" size={16} />
      <span class="sr-only">Search provider presets</span>
      <input bind:value={presetSearch} type="search" placeholder="Search providers…" />
    </label>
    {#if catalogTab === 'accounts'}
      <p class="provider-catalog-hint">
        Subscription providers use credentials stored in Inventory. GitHub Copilot supports device
        OAuth here; the other presets expect an exported token or credential JSON and do not start
        an authorization flow.
      </p>
    {:else if catalogTab === 'free'}
      <p class="provider-catalog-hint">
        Free-tier and local providers. Some free hosted services still require an API key.
      </p>
    {/if}
    <div class="preset-list">
      {#each filteredCatalog as preset}
        {@const presetLogo = logoUrl(preset)}
        {@const capabilities = capabilityLabels(preset)}
        <button type="button" class="preset-row" on:click={() => choosePreset(preset)}>
          <span class="provider-mark">
            {#if presetLogo}
              <img
                src={presetLogo}
                alt=""
                on:error={(event) => ((event.currentTarget as HTMLImageElement).hidden = true)}
              />
            {:else}
              {displayInitial(preset)}
            {/if}
          </span>
          <span class="preset-row-copy">
            <strong>{text(preset.name ?? preset.id)}</strong>
            <small>
              {text(preset.api_type, 'OpenAI compatible')}{capabilities.length
                ? ` · ${capabilities.join(' · ')}`
                : ''}
            </small>
          </span>
          <span class="preset-row-badges">
            {#if presetTier(preset) === 'free'}<span class="catalog-badge free">
                {text(preset.group) === 'local' ? 'Local' : 'Free'}
              </span>{/if}
            <span class="catalog-badge">{authLabel(preset)}</span>
          </span>
          <Icon name="arrow" size={15} />
        </button>
      {/each}
      {#if !filteredCatalog.length}
        <p class="workspace-empty-copy">No providers match this category and search.</p>
      {/if}
      <button type="button" class="preset-row custom" on:click={useCustomProvider}>
        <span class="provider-mark"><Icon name="plus" size={16} /></span>
        <span class="preset-row-copy">
          <strong>Provider not listed?</strong>
          <small>Add a custom compatible endpoint</small>
        </span>
        <Icon name="arrow" size={15} />
      </button>
    </div>
  {:else}
    {@const configurePreset = editing
      ? presetForProvider(editing)
      : catalog.find((preset) => text(preset.id) === selectedPresetId)}
    {@const configureLogo = configurePreset ? logoUrl(configurePreset) : ''}
    {@const configureAuth = configurePreset ? authKind(configurePreset) : 'key'}
    {@const configureKeyOptional = configurePreset ? keyOptional(configurePreset) : false}
    <form bind:this={providerForm} on:submit|preventDefault={submit}>
      <input type="hidden" name="catalog_id" value={text(draft.catalog_id, '')} />
      <input type="hidden" name="live_models" value={liveDiscovery ? 'true' : 'false'} />
      <div class="provider-config-intro">
        <span class="provider-mark large">
          {#if configureLogo}<img src={configureLogo} alt="" />{:else}{displayInitial(
              configurePreset ?? draft
            )}{/if}
        </span>
        <span>
          <strong>
            {text(
              configurePreset?.name,
              selectedPresetId === 'custom' ? 'Custom provider' : text(draft.id)
            )}
          </strong>
          <small>
            {configureAuth === 'local'
              ? 'Local connection'
              : configureAuth === 'oauth'
                ? 'Inventory-backed provider'
                : 'API provider'} · {bool(configurePreset?.live_models, true)
              ? 'Model discovery supported'
              : 'Configured catalog'}
          </small>
        </span>
        <span class="catalog-badge">{authLabel(configurePreset ?? draft)}</span>
      </div>
      <div class="field-grid">
        <label class="field">
          <span>Connection ID</span>
          <input name="id" required disabled={!!editing} value={text(draft.id, '')} />
          <small>Unique ID for this routing connection, not for an individual credential.</small>
        </label>
        <label class="field">
          <span>Routing prefix</span>
          <input name="prefix" required value={text(draft.prefix, '')} />
          <small>Clients call this provider as prefix/model.</small>
        </label>
        {#if selectedPresetId === 'custom' || (!selectedPresetId && !configurePreset)}
          <label class="field">
            <span>API type</span>
            <select name="api_type" value={text(draft.api_type, 'openai_compat')}>
              {#each apiTypes as apiType}<option value={apiType[0]}>{apiType[1]}</option>{/each}
            </select>
          </label>
        {:else}
          <input type="hidden" name="api_type" value={text(draft.api_type, 'openai_compat')} />
        {/if}
        <label class:full={selectedPresetId !== 'custom'} class="field">
          <span>{configureAuth === 'local' ? 'Local base URL' : 'Base URL'}</span>
          <input
            name="base_url"
            type="url"
            value={text(draft.base_url, '')}
            placeholder="https://api.example.com/v1"
          />
          <small>
            {configurePreset
              ? 'Preset default shown; change it only for a compatible deployment.'
              : 'Include the provider API version path when required.'}
          </small>
        </label>
        {#if configureAuth !== 'local'}
          <section class="provider-architecture-note full">
            <Icon name="route" size={18} />
            <div>
              <strong>Recommended: one routing connection, many Inventory credentials</strong>
              <p>
                Save this connection without a direct credential, then add every user or
                subscription credential in Inventory. Janus routes, cools down, and falls back
                across that shared pool.
              </p>
              <div class="provider-account-links">
                <a class="button" href="/dashboard/ui/inventory/add">Add credentials</a>
                <a class="button ghost" href="/dashboard/ui/inventory/import">Import JSON</a>
              </div>
            </div>
          </section>
        {/if}
        {#if text(draft.api_type, '') === 'github_copilot'}
          <section class="copilot-oauth full" aria-labelledby="copilot-oauth-title">
            <header>
              <div>
                <span class="workspace-section-label">Recommended</span>
                <h3 id="copilot-oauth-title">Connect GitHub Copilot</h3>
                <p>
                  Authorize Janus with GitHub's device flow. You can still paste a token manually
                  below.
                </p>
              </div>
              <button
                type="button"
                class="button"
                disabled={copilotStarting || copilotPolling}
                on:click={startCopilotOAuth}
              >
                <Icon name="key" size={14} />
                {copilotStarting
                  ? 'Starting…'
                  : copilotUserCode
                    ? 'Start again'
                    : 'Authorize with GitHub'}
              </button>
            </header>
            {#if copilotUserCode}
              <div class="copilot-device-code">
                <span>
                  <small>One-time code</small>
                  <code>{copilotUserCode}</code>
                </span>
                <a
                  class="button primary"
                  href={copilotVerificationUri}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open GitHub
                </a>
              </div>
              <p class="copilot-expiry">
                This code expires in about {Math.max(
                  1,
                  Math.ceil((copilotExpiresAt - Date.now()) / 60_000)
                )} minutes.
              </p>
            {/if}
            {#if copilotStatus}<p class="copilot-status" aria-live="polite">
                {copilotStatus}
              </p>{/if}
            {#if copilotError}<p class="copilot-error" role="alert">{copilotError}</p>{/if}
          </section>
        {:else if configureAuth === 'oauth'}
          <section class="provider-credential-guide full">
            <span class="workspace-section-label">Credential setup</span>
            <h3>
              Use Inventory for {text(configurePreset?.name, text(draft.id, 'this provider'))}
            </h3>
            <p>
              Janus does not implement an authorization flow for this provider in the dashboard.
              {credentialInstructions(configurePreset ?? draft)}
            </p>
            <div class="provider-account-links">
              <a class="button" href="/dashboard/ui/inventory/add">Add exported credential</a>
              <a class="button ghost" href="/dashboard/ui/inventory/import">Import JSON</a>
            </div>
          </section>
        {/if}
        {#if configureAuth !== 'local' || !configureKeyOptional}
          <label class="field full">
            <span>
              {text(draft.api_type, '') === 'github_copilot'
                ? 'GitHub token (optional direct bootstrap)'
                : configureAuth === 'oauth'
                  ? 'Optional direct bootstrap credential'
                  : 'Optional direct bootstrap API key'}
            </span>
            <input
              bind:this={apiKeyInput}
              name="api_key"
              type="password"
              autocomplete="new-password"
              placeholder={editing
                ? 'Leave blank to preserve the configured credential'
                : 'Optional — add credentials in Inventory instead'}
            />
            <small>
              {editing
                ? 'Leave blank to preserve the current write-only credential. For additional credentials, use Inventory.'
                : text(draft.api_type, '') === 'github_copilot'
                  ? 'The device flow can fill this optional bootstrap field. Add additional Copilot credentials in Inventory.'
                  : 'Use this only to bootstrap a single credential. The recommended shared pool is managed in Inventory.'}
            </small>
          </label>
        {/if}
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
            placeholder="Optional routing allowlist"
          />
        </label>
        <label class="check-field full">
          <input type="checkbox" bind:checked={liveDiscovery} />
          <span>
            <strong>Discover models from provider</strong>
            <br />
            Refresh the upstream catalog and merge it with configured custom models.
          </span>
        </label>
        <div class="field full">
          <div class="field-title">
            <span>Seed models</span>
            <button
              type="button"
              class="button ghost compact"
              disabled={fetchingModels}
              on:click={fetchModels}
            >
              <Icon name="refresh" size={13} />{fetchingModels ? 'Fetching…' : 'Fetch models'}
            </button>
          </div>
          <textarea
            name="models"
            required={!liveDiscovery}
            bind:value={modelText}
            placeholder="model-one, model-two"></textarea>
          <small>Fallback models used when live discovery is disabled or unavailable.</small>
        </div>
        <details class="provider-advanced full">
          <summary>Provider limits</summary>
          <div class="field-grid">
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
        </details>
      </div>
      <div class="form-actions split-actions">
        {#if !editing}<button type="button" class="button ghost" on:click={backToCatalog}>
            Back to catalog
          </button>{/if}
        <span></span>
        <button type="button" class="button" on:click={closeModal}>Cancel</button>
        <button class="button primary">{editing ? 'Save changes' : 'Create connection'}</button>
      </div>
    </form>
  {/if}
</Modal>
