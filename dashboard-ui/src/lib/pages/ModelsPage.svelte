<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, firstList, number, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

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

  let search = '';
  let selectedGroupKey = 'all';
  let collapsed: Record<string, boolean> = {};
  let busy = '';
  let customOpen = false;
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
  $: searchedGroups = filterGroups(allGroups, search);
  $: groups =
    selectedGroupKey === 'all'
      ? searchedGroups
      : searchedGroups.filter((group) => group.key === selectedGroupKey);
  $: visibleCount = models.filter(isVisible).length;

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

  function filterGroups(groupRows: ModelGroup[], queryValue: string): ModelGroup[] {
    const query = queryValue.trim().toLowerCase();
    if (!query) return groupRows;
    return groupRows
      .map((group) => {
        if ([group.label, group.prefix].some((value) => value.toLowerCase().includes(query))) {
          return group;
        }
        return {
          ...group,
          rows: group.rows.filter((model) =>
            [model.id, model.namespaced, model.display_name, model.source]
              .map((value) => text(value, '').toLowerCase())
              .some((value) => value.includes(query))
          )
        };
      })
      .filter((group) => group.rows.length > 0);
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

  function groupVisibleCount(group: ModelGroup): number {
    return group.rows.filter(isVisible).length;
  }

  function isCollapsed(group: ModelGroup): boolean {
    if (search.trim()) return false;
    return collapsed[group.key] ?? true;
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

  function groupActionable(group: ModelGroup): boolean {
    return group.rows.some((model) => !modelBlockedReason(group, model));
  }

  function providerContext(group: ModelGroup): string {
    if (group.providerRows.length > 1)
      return `${group.label} · ${group.providerRows.length} gateway connections`;
    return `${group.label} · ${group.prefix}`;
  }

  async function setModelVisibility(model: JsonObject, enabled: boolean) {
    const key = modelName(model);
    busy = key;
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
      busy = '';
    }
  }

  async function setProviderVisibility(group: ModelGroup, enabled: boolean) {
    if (!groupActionable(group)) return;
    busy = `provider:${group.key}`;
    try {
      await action('/dashboard/api/v2/model-visibility', {
        method: 'PUT',
        body: {
          scope: 'provider',
          provider: group.prefix,
          provider_kind: 'prefix',
          targets: group.rows.map((model) => ({ id: text(model.id, ''), native: false })),
          enabled
        },
        success: enabled
          ? `${group.label} models shown in the shared catalog`
          : `${group.label} models hidden from the shared catalog`
      });
    } catch {
      return;
    } finally {
      busy = '';
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
    }
    customOpen = false;
  }

  async function removeCustomModel(model: JsonObject) {
    const id = text(model.custom_id, '');
    if (!id || !confirm(`Delete custom model ${text(model.id)}?`)) return;
    try {
      await action(`/dashboard/api/v2/custom-models/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        success: 'Custom model deleted'
      });
    } catch {
      return;
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
      on:click={() => (selectedGroupKey = 'all')}
    >
      <span>
        <strong>All providers</strong>
        <small>{visibleCount}/{models.length} visible</small>
      </span>
      <Icon name="arrow" size={14} />
    </button>
    {#each allGroups as group}
      <button
        type="button"
        class:active={selectedGroupKey === group.key}
        on:click={() => (selectedGroupKey = group.key)}
      >
        <span class="provider-mark">{group.label.slice(0, 1).toUpperCase()}</span>
        <span>
          <strong>{group.label}</strong>
          <small>{groupVisibleCount(group)}/{group.rows.length} visible</small>
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
        <strong>{models.length}</strong>
      </div>
    </section>

    <div class="model-toolbar">
      <label class="model-search">
        <Icon name="search" size={16} />
        <span class="sr-only">Search models</span>
        <input bind:value={search} type="search" placeholder="Search model IDs…" />
      </label>
      <button class="button ghost" on:click={() => setAllCollapsed(true)}>Collapse all</button>
      <button class="button ghost" on:click={() => setAllCollapsed(false)}>Expand all</button>
    </div>

    {#if groups.length}
      <div class="model-groups">
        {#each groups as group}
          {@const visible = groupVisibleCount(group)}
          {@const actionableRows = group.rows.filter((model) => !modelBlockedReason(group, model))}
          {@const allVisible =
            actionableRows.length > 0 && actionableRows.every((model) => isVisible(model))}
          <section class="model-provider panel">
            <header class="model-provider-header">
              <button
                type="button"
                class="model-provider-title"
                aria-expanded={!isCollapsed(group)}
                on:click={() => toggleCollapsed(group)}
              >
                <span class="provider-mark">{group.label.slice(0, 1).toUpperCase()}</span>
                <span>
                  <strong>{group.label}</strong>
                  <small>
                    {group.prefix} · {visible}/{group.rows.length} visible{group.providerRows
                      .length > 1
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
                {#if group.rows.length}
                  <button
                    class="button"
                    disabled={busy === `provider:${group.key}` || !groupActionable(group)}
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

            {#if !isCollapsed(group)}
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
                        disabled={busy === modelName(model) || !!blockedReason}
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
                            on:click={() => removeCustomModel(model)}
                          >
                            <Icon name="trash" size={14} />
                          </button>
                        </div>
                      {/if}
                    </article>
                  {/each}
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
      <button class="button primary" disabled={!customProviderId || !customModelId.trim()}>
        {editingCustom ? 'Save model' : 'Add custom model'}
      </button>
    </div>
  </form>
</Modal>
