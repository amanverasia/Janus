<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, firstList, number, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;
  export let navigateQuery: (params: Record<string, string>) => void;

  type ModelGroup = {
    key: string;
    label: string;
    prefix: string;
    providerId: string;
    providerRows: JsonObject[];
    rows: JsonObject[];
  };

  const modalities = ['text', 'image', 'audio'];
  const reasoningEfforts = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];

  let searchInput = '';
  let searchDirty = false;
  let selectedGroupKey = 'all';
  let appliedProviderKey: string | undefined;
  let collapsed: Record<string, boolean> = {};
  let busy = new Set<string>();
  let customOpen = false;
  let savingCustom = false;
  let editingCustom: JsonObject | undefined;
  let customRecordId = '';
  let customProviderId = '';
  let customProviderLabel = '';
  let customModelId = '';
  let customDisplayName = '';
  let customContextWindow = '';
  let customMaxOutputTokens = '';
  let customModalities: string[] = ['text'];
  let customReasoningEfforts: string[] = [];

  $: models = firstList(data, 'models', 'items');
  $: providers = firstList(data, 'providers');
  $: allGroups = buildGroups(models, providers);
  $: catalogTotal = number(data.model_total, models.length);
  $: visibleCount = number(data.visible_total, models.filter(isVisible).length);
  $: matchTotal = number(data.match_total, models.length);
  $: truncated = bool(data.truncated);
  $: appliedSearch = text(data.search, '');
  $: urlProvider = text(data.provider, '');
  // The server ships model rows only for a selected provider or a search; the
  // unfiltered view is a provider overview built from per-provider counts.
  $: browsing = selectedGroupKey !== 'all' || !!appliedSearch.trim();
  $: groups =
    selectedGroupKey !== 'all'
      ? allGroups.filter((group) => group.key === selectedGroupKey)
      : appliedSearch.trim()
        ? allGroups.filter((group) => group.rows.length > 0)
        : allGroups;
  $: if (appliedSearch !== searchInput && !searchDirty) searchInput = appliedSearch;
  $: if (urlProvider !== appliedProviderKey) {
    appliedProviderKey = urlProvider;
    selectedGroupKey = providerGroupKey(urlProvider, allGroups);
  }

  function runSearch() {
    searchDirty = false;
    navigateQuery({ search: searchInput.trim(), offset: '' });
  }

  function providerGroupKey(prefix: string, groupRows: ModelGroup[]): string {
    return prefix && groupRows.some((group) => group.key === prefix) ? prefix : 'all';
  }

  function selectGroup(key: string) {
    selectedGroupKey = key;
    navigate(
      key === 'all'
        ? '/dashboard/ui/models'
        : `/dashboard/ui/models?provider=${encodeURIComponent(key)}`
    );
  }

  function buildGroups(modelRows: JsonObject[], providerRows: JsonObject[]): ModelGroup[] {
    const grouped = new Map<string, ModelGroup>();
    for (const provider of providerRows) {
      const prefix = text(provider.prefix, text(provider.catalog_id, text(provider.id, 'unknown')));
      const current = grouped.get(prefix);
      if (current) {
        current.providerRows.push(provider);
        if (!current.providerId) current.providerId = text(provider.id, '');
        continue;
      }
      grouped.set(prefix, {
        key: prefix,
        label: text(provider.name ?? provider.catalog_id ?? provider.prefix ?? provider.id, prefix),
        prefix,
        providerId: text(provider.id, ''),
        providerRows: [provider],
        rows: []
      });
    }
    for (const row of modelRows) {
      const prefix = text(row.prefix ?? row.provider, 'unknown');
      const current = grouped.get(prefix);
      if (current) {
        current.rows.push(row);
        if (!current.providerId) current.providerId = text(row.provider_id, '');
        if (current.label === current.prefix) {
          current.label = text(row.provider_name ?? row.provider, current.label);
        }
        continue;
      }
      grouped.set(prefix, {
        key: prefix,
        label: text(row.provider_name ?? row.provider ?? row.prefix, prefix),
        prefix,
        providerId: text(row.provider_id, ''),
        providerRows: [],
        rows: [row]
      });
    }
    return [...grouped.values()].sort(
      (left, right) =>
        Number(groupEnabled(right)) - Number(groupEnabled(left)) ||
        left.label.localeCompare(right.label)
    );
  }

  function isVisible(model: JsonObject): boolean {
    return !bool(model.disabled);
  }

  function modelName(model: JsonObject): string {
    return text(model.namespaced, `${text(model.prefix, '')}/${text(model.id, '')}`);
  }

  function listValue(value: unknown): string[] {
    return Array.isArray(value) ? value.map((item) => text(item, '')).filter(Boolean) : [];
  }

  // Provider-wide counts come from the server so they never depend on which
  // rows happen to be loaded; loaded rows are only a fallback.
  function groupCount(group: ModelGroup, key: string, fallback: number): number {
    const source = group.providerRows.find((provider) => provider[key] !== undefined);
    return source ? number(source[key]) : fallback;
  }

  function groupTotal(group: ModelGroup): number {
    return groupCount(group, 'model_count', group.rows.length);
  }

  function groupVisibleCount(group: ModelGroup): number {
    return groupCount(group, 'visible_model_count', group.rows.filter(isVisible).length);
  }

  function isCollapsed(group: ModelGroup): boolean {
    if (!browsing) return true;
    return collapsed[group.key] ?? false;
  }

  function toggleCollapsed(group: ModelGroup) {
    collapsed = { ...collapsed, [group.key]: !isCollapsed(group) };
  }

  function setAllCollapsed(value: boolean) {
    collapsed = Object.fromEntries(allGroups.map((group) => [group.key, value]));
  }

  function groupEnabled(group: ModelGroup): boolean {
    return group.providerRows.some((provider) => bool(provider.is_enabled, true));
  }

  function modelBlockedReason(group: ModelGroup, model: JsonObject): string {
    if (!groupEnabled(group)) return 'Enable this provider before changing model visibility.';
    if (model.provider_enabled !== undefined && !bool(model.provider_enabled)) {
      return 'Enable the model provider before changing visibility.';
    }
    if (model.custom_enabled !== undefined && !bool(model.custom_enabled)) {
      return 'This custom model is disabled. Edit it to enable it before changing visibility.';
    }
    return '';
  }

  function toggleableRows(group: ModelGroup): JsonObject[] {
    return group.rows.filter((model) => !modelBlockedReason(group, model));
  }

  function groupActionable(group: ModelGroup): boolean {
    if (!groupEnabled(group)) return false;
    return groupCount(group, 'toggleable_model_count', toggleableRows(group).length) > 0;
  }

  function groupAllVisible(group: ModelGroup): boolean {
    const rows = toggleableRows(group);
    const toggleable = groupCount(group, 'toggleable_model_count', rows.length);
    const visible = groupCount(group, 'toggleable_visible_count', rows.filter(isVisible).length);
    return toggleable > 0 && visible === toggleable;
  }

  function providerContext(group: ModelGroup): string {
    if (group.providerRows.length > 1)
      return `${group.label} · ${group.providerRows.length} gateway connections`;
    return `${group.label} · ${group.prefix}`;
  }

  async function setModelVisibility(model: JsonObject, enabled: boolean) {
    const key = modelName(model);
    if (busy.has(key)) return;
    busy = new Set(busy).add(key);
    try {
      await action('/dashboard/api/v2/model-visibility', {
        method: 'PUT',
        body: {
          scope: 'models',
          provider: text(model.prefix, ''),
          provider_kind: 'prefix',
          targets: [{ id: text(model.id, ''), native: false }],
          enabled
        },
        success: enabled
          ? 'Model shown in the shared catalog'
          : 'Model hidden from the shared catalog'
      });
    } catch {
      return;
    } finally {
      const next = new Set(busy);
      next.delete(key);
      busy = next;
    }
  }

  async function setProviderVisibility(group: ModelGroup, enabled: boolean) {
    if (!groupActionable(group)) return;
    const key = `provider:${group.key}`;
    if (busy.has(key)) return;
    busy = new Set(busy).add(key);
    try {
      await action('/dashboard/api/v2/model-visibility', {
        method: 'PUT',
        body: {
          scope: 'provider',
          provider: group.prefix,
          provider_kind: 'prefix',
          // Provider scope flips every model of the provider server-side,
          // whichever rows are loaded here.
          targets: [],
          enabled
        },
        success: enabled
          ? `${group.label} models shown in the shared catalog`
          : `${group.label} models hidden from the shared catalog`
      });
    } catch {
      return;
    } finally {
      const next = new Set(busy);
      next.delete(key);
      busy = next;
    }
  }

  function openCustom(group: ModelGroup, model?: JsonObject) {
    if (!group.providerId) return;
    editingCustom = model;
    customRecordId = text(model?.custom_id, '');
    customProviderId = text(model?.provider_id, group.providerId);
    customProviderLabel = providerContext(group);
    customModelId = text(model?.id, '');
    customDisplayName = text(model?.display_name, '');
    customContextWindow = model?.context_window == null ? '' : text(model.context_window, '');
    customMaxOutputTokens =
      model?.max_output_tokens == null ? '' : text(model.max_output_tokens, '');
    customModalities = model ? listValue(model.input_modalities) : ['text'];
    customReasoningEfforts = model ? listValue(model.reasoning_efforts) : [];
    customOpen = true;
  }

  function optionalNumber(value: string): number | undefined {
    if (!value.trim()) return undefined;
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
  }

  async function saveCustomModel() {
    if (savingCustom) return;
    const body: JsonObject = {
      provider_id: customProviderId,
      model_id: customModelId.trim(),
      display_name: customDisplayName.trim() || null,
      context_window: optionalNumber(customContextWindow) ?? null,
      max_output_tokens: optionalNumber(customMaxOutputTokens) ?? null,
      input_modalities: customModalities,
      reasoning_efforts: customReasoningEfforts,
      is_enabled: true
    };
    savingCustom = true;
    try {
      await action(
        editingCustom
          ? `/dashboard/api/v2/custom-models/${encodeURIComponent(customRecordId)}`
          : '/dashboard/api/v2/custom-models',
        {
          method: editingCustom ? 'PUT' : 'POST',
          body,
          success: editingCustom ? 'Custom model updated' : 'Custom model added'
        }
      );
    } catch {
      return;
    } finally {
      savingCustom = false;
    }
    customOpen = false;
  }

  async function removeCustomModel(model: JsonObject) {
    const id = text(model.custom_id, '');
    if (!id || busy.has(id) || !confirm(`Delete custom model ${text(model.id)}?`)) return;
    busy = new Set(busy).add(id);
    try {
      await action(`/dashboard/api/v2/custom-models/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        success: 'Custom model deleted'
      });
    } finally {
      const next = new Set(busy);
      next.delete(id);
      busy = next;
    }
  }
</script>

<PageHeader
  title="Models"
  description="Choose which models appear in the shared Janus catalog. Hidden models remain callable by exact ID."
/>

<div class="model-workspace">
  <aside class="model-provider-rail" aria-label="Models">
    <div class="model-provider-rail-head">
      <span>Providers</span>
      <strong>{allGroups.length}</strong>
    </div>
    <button
      type="button"
      class:active={selectedGroupKey === 'all'}
      on:click={() => selectGroup('all')}
    >
      <span>
        <strong>All providers</strong>
        <small>{visibleCount}/{catalogTotal} visible</small>
      </span>
      <Icon name="arrow" size={14} />
    </button>
    {#each allGroups as group}
      <button
        type="button"
        class:active={selectedGroupKey === group.key}
        on:click={() => selectGroup(group.key)}
      >
        <span class="provider-mark">{group.label.slice(0, 1).toUpperCase()}</span>
        <span>
          <strong>{group.label}</strong>
          <small>
            {groupTotal(group)
              ? `${groupVisibleCount(group)}/${groupTotal(group)} visible`
              : 'No models yet'}
          </small>
        </span>
        <span
          class:active={groupEnabled(group)}
          class="provider-status-dot"
          title={groupEnabled(group) ? 'Enabled' : 'Disabled'}
        ></span>
      </button>
    {/each}
  </aside>

  <main class="model-workspace-detail" aria-label="Model details">
    <section class="model-overview" aria-label="Model catalog summary">
      <div>
        <span>Providers</span>
        <strong>{allGroups.length}</strong>
      </div>
      <div>
        <span>Visible</span>
        <strong>{visibleCount}</strong>
      </div>
      <div>
        <span>Total catalog</span>
        <strong>{catalogTotal}</strong>
      </div>
    </section>

    <div class="model-toolbar">
      <label class="model-search">
        <Icon name="search" size={16} />
        <span class="sr-only">Search models</span>
        <input
          bind:value={searchInput}
          type="search"
          placeholder="Search model IDs…"
          on:input={() => (searchDirty = true)}
          on:keydown={(event) => event.key === 'Enter' && runSearch()}
        />
      </label>
      {#if appliedSearch}
        <button
          class="button ghost"
          on:click={() => {
            searchInput = '';
            runSearch();
          }}
        >
          Clear
        </button>
      {/if}
      {#if browsing && groups.length > 1}
        <button class="button ghost" on:click={() => setAllCollapsed(true)}>Collapse all</button>
        <button class="button ghost" on:click={() => setAllCollapsed(false)}>Expand all</button>
      {/if}
    </div>

    {#if appliedSearch.trim()}
      <p class="model-search-summary" role="status">
        {truncated
          ? `Showing the first ${models.length} of ${matchTotal} matches. Narrow the search or pick a provider to see the rest.`
          : `${matchTotal} ${matchTotal === 1 ? 'match' : 'matches'}`}
      </p>
    {/if}

    {#if groups.length}
      <div class="model-groups">
        {#each groups as group}
          {@const visible = groupVisibleCount(group)}
          {@const total = groupTotal(group)}
          {@const allVisible = groupAllVisible(group)}
          <section class="model-provider panel">
            <header class="model-provider-header">
              <button
                type="button"
                class="model-provider-title"
                aria-expanded={browsing ? !isCollapsed(group) : undefined}
                title={browsing ? undefined : `Browse ${group.label} models`}
                on:click={() => (browsing ? toggleCollapsed(group) : selectGroup(group.key))}
              >
                <span class="provider-mark">{group.label.slice(0, 1).toUpperCase()}</span>
                <span>
                  <strong>{group.label}</strong>
                  <small>
                    {group.prefix} · {visible}/{total} visible{appliedSearch.trim()
                      ? ` · ${group.rows.length} ${group.rows.length === 1 ? 'match' : 'matches'}`
                      : ''}{group.providerRows.length > 1
                      ? ` · ${group.providerRows.length} gateway connections`
                      : ''}
                  </small>
                </span>
                <Icon name="arrow" size={15} />
              </button>
              <div class="model-provider-actions">
                <button
                  class="button ghost"
                  disabled={!group.providerId}
                  title={group.providerId
                    ? 'Add a custom model to this provider'
                    : 'No configured provider row is available'}
                  on:click={() => openCustom(group)}
                >
                  <Icon name="plus" size={14} />Add custom model
                </button>
                {#if !browsing && total}
                  <button class="button ghost" on:click={() => selectGroup(group.key)}>
                    Browse {total} models
                  </button>
                {/if}
                {#if total}
                  <button
                    class="button"
                    disabled={busy.has(`provider:${group.key}`) || !groupActionable(group)}
                    title={groupActionable(group)
                      ? allVisible
                        ? 'Hide every actionable model for this provider'
                        : 'Show every actionable model for this provider'
                      : 'Enable this provider or its custom models before changing visibility'}
                    on:click={() => setProviderVisibility(group, !allVisible)}
                  >
                    {allVisible ? 'All off' : 'All on'}
                  </button>
                {/if}
              </div>
            </header>

            {#if !isCollapsed(group) || !total}
              {#if group.rows.length}
                <div class="model-list">
                  {#each group.rows as model}
                    {@const enabled = isVisible(model)}
                    {@const blockedReason = modelBlockedReason(group, model)}
                    {@const efforts = listValue(model.reasoning_efforts)}
                    {@const inputs = listValue(model.input_modalities)}
                    <article class:disabled={!enabled} class="model-row">
                      <button
                        type="button"
                        class:enabled
                        class="model-toggle"
                        role="switch"
                        aria-checked={enabled}
                        aria-label={`${enabled ? 'Hide' : 'Show'} ${modelName(model)}`}
                        title={blockedReason || `${enabled ? 'Hide' : 'Show'} ${modelName(model)}`}
                        disabled={busy.has(modelName(model)) || !!blockedReason}
                        on:click={() => setModelVisibility(model, !enabled)}
                      >
                        <span></span>
                      </button>
                      <div class="model-main">
                        <div class="model-name">
                          <code>{modelName(model)}</code>
                          {#if bool(model.default)}<span class="model-badge default">
                              Default
                            </span>{/if}
                          <span class="model-badge">{text(model.source, 'configured')}</span>
                        </div>
                        <div class="model-meta">
                          {#if model.context_window}<span>
                              {number(model.context_window).toLocaleString()} context
                            </span>{/if}
                          {#if model.max_output_tokens}<span>
                              {number(model.max_output_tokens).toLocaleString()} output
                            </span>{/if}
                          {#if inputs.length}<span>{inputs.join(' + ')}</span>{/if}
                          {#if efforts.length}<span>{efforts.join(' / ')}</span>{/if}
                        </div>
                      </div>
                      {#if text(model.source, '') === 'custom' && text(model.custom_id, '')}
                        <div class="model-row-actions">
                          <button
                            class="icon-button"
                            aria-label="Edit custom model"
                            on:click={() => openCustom(group, model)}
                          >
                            <Icon name="edit" size={14} />
                          </button>
                          <button
                            class="icon-button"
                            aria-label="Delete custom model"
                            disabled={busy.has(text(model.custom_id, ''))}
                            on:click={() => removeCustomModel(model)}
                          >
                            <Icon name="trash" size={14} />
                          </button>
                        </div>
                      {/if}
                    </article>
                  {/each}
                </div>
              {:else if appliedSearch.trim()}
                <div class="model-provider-empty">
                  <Icon name="search" size={18} />
                  <div>
                    <strong>No models match</strong>
                    <p>No {group.label} model IDs match “{appliedSearch}”.</p>
                  </div>
                </div>
              {:else}
                <div class="model-provider-empty">
                  <Icon name={groupEnabled(group) ? 'refresh' : 'warning'} size={18} />
                  <div>
                    <strong>
                      {groupEnabled(group) ? 'No models cached yet' : 'Provider is disabled'}
                    </strong>
                    <p>
                      {groupEnabled(group)
                        ? 'Fetch models from the provider settings, wait for discovery, or add a custom model here.'
                        : 'Enable this provider from the Providers page before model discovery can run.'}
                    </p>
                  </div>
                  {#if group.providerId}<button class="button" on:click={() => openCustom(group)}>
                      <Icon name="plus" size={14} />Add custom model
                    </button>{/if}
                </div>
              {/if}
            {/if}
          </section>
        {/each}
      </div>
    {:else}
      <section class="panel">
        <EmptyState
          icon="layers"
          title={allGroups.length ? 'No models match' : 'No providers configured'}
          message={allGroups.length
            ? 'Try another provider or model search.'
            : 'Connect a provider before managing its catalog.'}
        />
      </section>
    {/if}
  </main>
</div>

<Modal
  open={customOpen}
  title={editingCustom ? 'Edit custom model' : 'Add custom model'}
  description="The provider is fixed by the model group you opened. Custom metadata augments that provider only."
  wide
  on:close={() => (customOpen = false)}
>
  <form on:submit|preventDefault={saveCustomModel}>
    <div class="custom-model-context">
      <span class="provider-mark">{customProviderLabel.slice(0, 1).toUpperCase()}</span>
      <span>
        <small>Provider</small>
        <strong>{customProviderLabel}</strong>
      </span>
    </div>
    <div class="field-grid">
      <label class="field full">
        <span>Model ID</span>
        <input bind:value={customModelId} required placeholder="model-endpoint-slug" />
        <small>The final routed ID will use the provider prefix shown above.</small>
      </label>
      <label class="field full">
        <span>Display name</span>
        <input bind:value={customDisplayName} placeholder="Optional catalog label" />
      </label>
      <label class="field">
        <span>Context window</span>
        <input bind:value={customContextWindow} type="number" min="1" placeholder="Optional" />
      </label>
      <label class="field">
        <span>Maximum output tokens</span>
        <input bind:value={customMaxOutputTokens} type="number" min="1" placeholder="Optional" />
      </label>
      <fieldset class="capability-field full">
        <legend>Input modalities</legend>
        <div class="capability-options">
          {#each modalities as modality}<label>
              <input type="checkbox" bind:group={customModalities} value={modality} />
              {modality}
            </label>{/each}
        </div>
      </fieldset>
      <fieldset class="capability-field full">
        <legend>Reasoning efforts</legend>
        <div class="capability-options">
          {#each reasoningEfforts as effort}<label>
              <input type="checkbox" bind:group={customReasoningEfforts} value={effort} />
              {effort}
            </label>{/each}
        </div>
      </fieldset>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (customOpen = false)}>Cancel</button>
      <button
        class="button primary"
        disabled={savingCustom || !customProviderId || !customModelId.trim()}
      >
        {editingCustom ? 'Save model' : 'Add custom model'}
      </button>
    </div>
  </form>
</Modal>
