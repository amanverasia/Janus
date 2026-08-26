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
    rows: JsonObject[];
  };

  const modalities = ['text', 'image', 'audio'];
  const reasoningEfforts = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

  let search = '';
  let collapsed: Record<string, boolean> = {};
  let busy = '';
  let customOpen = false;
  let editingCustom: JsonObject | undefined;
  let customRecordId = '';
  let customProviderId = '';
  let customModelId = '';
  let customDisplayName = '';
  let customContextWindow = '';
  let customMaxOutputTokens = '';
  let customModalities: string[] = ['text'];
  let customReasoningEfforts: string[] = [];

  $: models = firstList(data, 'models', 'items');
  $: providers = firstList(data, 'providers');
  $: allGroups = groupModels(models);
  $: groups = groupModels(
    models.filter((model) => {
      const query = search.trim().toLowerCase();
      if (!query) return true;
      return [
        model.provider,
        model.prefix,
        model.id,
        model.namespaced,
        model.display_name,
        model.source
      ]
        .map((value) => text(value, '').toLowerCase())
        .some((value) => value.includes(query));
    })
  );
  $: visibleCount = models.filter(isVisible).length;
  $: providerOptions = customProviderOptions(providers, allGroups);

  function customProviderOptions(rows: JsonObject[], groups: ModelGroup[]) {
    const choices = new Map<string, { value: string; label: string; provider: string }>();
    for (const provider of rows) {
      const value = text(provider.id, '');
      if (!value) continue;
      const catalogLabel = text(provider.catalog_id ?? provider.prefix ?? provider.id);
      choices.set(value, {
        value,
        label: catalogLabel === value ? catalogLabel : `${catalogLabel} · ${value}`,
        provider: catalogLabel
      });
    }
    for (const group of groups) {
      if (choices.has(group.providerId)) continue;
      choices.set(group.providerId, {
        value: group.providerId,
        label: group.label,
        provider: group.key
      });
    }
    return [...choices.values()].sort((left, right) => left.label.localeCompare(right.label));
  }

  function groupModels(rows: JsonObject[]): ModelGroup[] {
    const grouped = new Map<string, ModelGroup>();
    for (const row of rows) {
      const key = text(row.provider ?? row.prefix, 'unknown');
      const current = grouped.get(key);
      if (current) {
        current.rows.push(row);
        continue;
      }
      grouped.set(key, {
        key,
        label: text(row.provider_name ?? row.provider ?? row.prefix, key),
        prefix: text(row.prefix, key),
        providerId: text(row.provider_id, key),
        rows: [row]
      });
    }
    return [...grouped.values()].sort((left, right) => left.label.localeCompare(right.label));
  }

  function isVisible(model: JsonObject): boolean {
    return !bool(model.disabled);
  }

  function providerTarget(model: JsonObject): string {
    return text(model.prefix, '');
  }

  function modelName(model: JsonObject): string {
    return text(model.namespaced, `${text(model.prefix, '')}/${text(model.id, '')}`);
  }

  function listValue(value: unknown): string[] {
    return Array.isArray(value) ? value.map((item) => text(item, '')).filter(Boolean) : [];
  }

  function fullRows(group: ModelGroup): JsonObject[] {
    return allGroups.find((candidate) => candidate.key === group.key)?.rows ?? group.rows;
  }

  function toggleCollapsed(group: ModelGroup) {
    collapsed = { ...collapsed, [group.key]: !collapsed[group.key] };
  }

  function setAllCollapsed(value: boolean) {
    collapsed = Object.fromEntries(allGroups.map((group) => [group.key, value]));
  }

  async function setModelVisibility(model: JsonObject, enabled: boolean) {
    const key = modelName(model);
    busy = key;
    try {
      await action('/dashboard/api/v2/model-visibility', {
        method: 'PUT',
        body: {
          scope: 'models',
          provider: providerTarget(model),
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
    busy = `provider:${group.key}`;
    try {
      await action('/dashboard/api/v2/model-visibility', {
        method: 'PUT',
        body: {
          scope: 'provider',
          provider: group.prefix,
          provider_kind: 'prefix',
          targets: fullRows(group).map((model) => ({ id: text(model.id, ''), native: false })),
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

  function openCustom(group?: ModelGroup, model?: JsonObject) {
    editingCustom = model;
    customRecordId = text(model?.custom_id, '');
    customProviderId = model
      ? text(model.provider_id, '')
      : (group?.providerId ?? providerOptions[0]?.value ?? '');
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
  description="Control the shared model catalog exposed by this Janus gateway. Hidden models remain routable by exact ID."
>
  <button class="button primary" disabled={!providerOptions.length} on:click={() => openCustom()}>
    <Icon name="plus" />Add custom model
  </button>
</PageHeader>

<section class="model-overview" aria-label="Model catalog summary">
  <div>
    <span>Providers</span>
    <strong>{providers.length || allGroups.length}</strong>
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
    <input bind:value={search} type="search" placeholder="Search providers or model IDs…" />
  </label>
  <button class="button ghost" on:click={() => setAllCollapsed(false)}>Expand all</button>
  <button class="button ghost" on:click={() => setAllCollapsed(true)}>Collapse all</button>
</div>

{#if groups.length}
  <div class="model-groups">
    {#each groups as group}
      {@const full = fullRows(group)}
      {@const visible = full.filter(isVisible).length}
      {@const allVisible = visible === full.length}
      <section class="model-provider panel">
        <header class="model-provider-header">
          <button
            type="button"
            class="model-provider-title"
            aria-expanded={!collapsed[group.key]}
            on:click={() => toggleCollapsed(group)}
          >
            <span class="provider-mark">{group.label.slice(0, 1)}</span>
            <span>
              <strong>{group.label}</strong>
              <small>{group.prefix} · {visible}/{full.length} visible</small>
            </span>
            <Icon name="arrow" size={15} />
          </button>
          <div class="model-provider-actions">
            <button class="button ghost" on:click={() => openCustom(group)}>
              <Icon name="plus" size={14} />Custom
            </button>
            <button
              class="button"
              disabled={busy === `provider:${group.key}`}
              on:click={() => setProviderVisibility(group, !allVisible)}
            >
              {allVisible ? 'Hide all' : 'Show all'}
            </button>
          </div>
        </header>

        {#if !collapsed[group.key]}
          <div class="model-list">
            {#each group.rows as model}
              {@const enabled = isVisible(model)}
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
                  disabled={busy === modelName(model)}
                  on:click={() => setModelVisibility(model, !enabled)}
                >
                  <span></span>
                </button>
                <div class="model-main">
                  <div class="model-name">
                    <code>{modelName(model)}</code>
                    {#if bool(model.default)}<span class="model-badge default">Default</span>{/if}
                    <span class="model-badge">{text(model.source, 'configured')}</span>
                  </div>
                  <div class="model-meta">
                    {#if model.context_window}
                      <span>{number(model.context_window).toLocaleString()} context</span>
                    {/if}
                    {#if model.max_output_tokens}
                      <span>{number(model.max_output_tokens).toLocaleString()} output</span>
                    {/if}
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
        {/if}
      </section>
    {/each}
  </div>
{:else}
  <section class="panel">
    <EmptyState
      icon="layers"
      title={models.length ? 'No models match' : 'No models discovered'}
      message={models.length
        ? 'Try a different provider or model search.'
        : 'Connect a provider or add a custom model to populate the shared gateway catalog.'}
    />
  </section>
{/if}

<Modal
  open={customOpen}
  title={editingCustom ? 'Edit custom model' : 'Add custom model'}
  description="Custom metadata fills catalog gaps without changing the upstream credential configuration."
  wide
  on:close={() => (customOpen = false)}
>
  <form on:submit|preventDefault={saveCustomModel}>
    <div class="field-grid">
      <label class="field">
        <span>Provider</span>
        <select bind:value={customProviderId} disabled={!!editingCustom} required>
          {#each providerOptions as provider}
            <option value={provider.value}>{provider.label}</option>
          {/each}
        </select>
      </label>
      <label class="field">
        <span>Model ID</span>
        <input bind:value={customModelId} required placeholder="model-endpoint-slug" />
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
          {#each modalities as modality}
            <label>
              <input type="checkbox" bind:group={customModalities} value={modality} />
              {modality}
            </label>
          {/each}
        </div>
      </fieldset>
      <fieldset class="capability-field full">
        <legend>Reasoning efforts</legend>
        <div class="capability-options">
          {#each reasoningEfforts as effort}
            <label>
              <input type="checkbox" bind:group={customReasoningEfforts} value={effort} />
              {effort}
            </label>
          {/each}
        </div>
      </fieldset>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (customOpen = false)}>Cancel</button>
      <button class="button primary" disabled={!customProviderId || !customModelId.trim()}>
        {editingCustom ? 'Save model' : 'Add model'}
      </button>
    </div>
  </form>
</Modal>
