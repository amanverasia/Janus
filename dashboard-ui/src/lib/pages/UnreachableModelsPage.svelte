<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import Pagination from '$lib/components/Pagination.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import { compact, entries, firstList, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  export let data: JsonObject;
  export let navigate: (href: string) => void;
  export let navigateQuery: (params: Record<string, string>) => void;

  const reasonLabels: Record<string, string> = {
    no_provider: 'No provider',
    provider_disabled: 'Provider disabled',
    no_active_credential: 'No active credential',
    model_not_enabled: 'Model not enabled'
  };

  const sourceLabels: Record<string, string> = {
    catalog: 'Catalog',
    discovered: 'Discovered',
    configured: 'Configured'
  };

  $: groups = firstList(data, 'groups');
  $: models = firstList(data, 'models');
  $: soon = firstList(data, 'soon');
  $: unreachableTotal = number(data.unreachable_total);
  $: reachableTotal = number(data.reachable_total);
  $: filteredTotal = number(data.total, models.length);
  $: provider = text(data.provider, '');
  $: reason = text(data.reason, '');
  $: search = text(data.search, '');
  $: filtered = Boolean(provider || reason || search);
  $: names = new Map(groups.map((group) => [text(group.prefix, ''), groupName(group)]));

  let searchInput = '';
  let searchDirty = false;
  $: if (searchDirty) {
    if (search === searchInput.trim()) searchDirty = false;
  } else if (search !== searchInput) {
    searchInput = search;
  }

  function reasonLabel(value: unknown): string {
    const key = text(value, '');
    return reasonLabels[key] ?? key;
  }

  function groupName(group: JsonObject): string {
    return text(group.name, text(group.prefix, 'Provider'));
  }

  function connectLabel(group: JsonObject): string {
    return text(object(group.connect).kind, '') === 'connect'
      ? `Connect ${groupName(group)}`
      : 'Add provider';
  }

  function openConnect(group: JsonObject) {
    const href = text(object(group.connect).href, '');
    if (href) navigate(href);
  }

  function applyFilters(next: Partial<Record<'provider' | 'reason' | 'search', string>>) {
    navigateQuery({
      provider: next.provider ?? provider,
      reason: next.reason ?? reason,
      search: next.search ?? search,
      offset: ''
    });
  }

  function selectValue(event: Event): string {
    return (event.currentTarget as HTMLSelectElement).value;
  }

  function runSearch() {
    applyFilters({ search: searchInput.trim() });
  }

  function clearFilters() {
    searchInput = '';
    navigateQuery({ provider: '', reason: '', search: '', offset: '' });
  }
</script>

<PageHeader
  title="Models you can't reach"
  description="Known models Janus can't route right now, and the credential that unlocks them."
/>

<div class="stats-grid">
  <StatCard
    label="Can't reach"
    value={compact(unreachableTotal)}
    detail={`Across ${compact(groups.length)} providers`}
    tone="amber"
  />
  <StatCard
    label="Reachable"
    value={compact(reachableTotal)}
    detail="Known models with a route"
    tone="teal"
  />
  <StatCard
    label="Reachable soon"
    value={compact(soon.length)}
    detail="Every account cooling down"
    tone="violet"
  />
</div>

{#if soon.length}
  <section class="panel soon-panel">
    <div class="panel-header">
      <div>
        <h2>Reachable soon</h2>
        <p>
          Every account for these models is cooling down; they'll route again when the cooldown
          ends.
        </p>
      </div>
    </div>
    <div class="panel-body soon-list">
      {#each soon as item}
        <span class="status cooldown mono">{text(item.prefix, '')}/{text(item.model, '')}</span>
      {/each}
    </div>
  </section>
{/if}

{#if unreachableTotal === 0}
  <section class="panel">
    <EmptyState icon="layers" title="Nothing to unlock" message="Every known model is reachable." />
  </section>
{:else}
  <div class="group-grid">
    {#each groups as group}
      <article class="panel group-card">
        <header>
          <div>
            <h3>{groupName(group)}</h3>
            <small class="mono">{text(group.prefix)}</small>
          </div>
          <span class="group-count">
            {compact(group.count)}
            {number(group.count) === 1 ? 'model' : 'models'}
          </span>
        </header>
        <div class="chips">
          {#each entries(group.reasons) as [key, count]}
            <span class="status warning">{reasonLabel(key)} · {compact(count)}</span>
          {/each}
        </div>
        <ul class="samples">
          {#each Array.isArray(group.sample_models) ? group.sample_models : [] as sample}
            <li class="mono">{text(sample, '')}</li>
          {/each}
        </ul>
        <button class="button primary" on:click={() => openConnect(group)}>
          <Icon name="plus" />{connectLabel(group)}
        </button>
      </article>
    {/each}
  </div>

  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Unreachable models</h2>
        <p>{compact(filteredTotal)} {filtered ? 'matching' : 'known'} models without a route</p>
      </div>
    </div>
    <div class="filterbar model-filters">
      <select
        aria-label="Filter by provider"
        value={provider}
        on:change={(event) => applyFilters({ provider: selectValue(event) })}
      >
        <option value="">All providers</option>
        {#each groups as group}
          <option value={text(group.prefix, '')}>{groupName(group)}</option>
        {/each}
      </select>
      <select
        aria-label="Filter by reason"
        value={reason}
        on:change={(event) => applyFilters({ reason: selectValue(event) })}
      >
        <option value="">All reasons</option>
        {#each Object.entries(reasonLabels) as [key, label]}
          <option value={key}>{label}</option>
        {/each}
      </select>
      <input
        type="search"
        placeholder="Search model ids…"
        aria-label="Search unreachable models"
        bind:value={searchInput}
        on:input={() => (searchDirty = true)}
        on:keydown={(event) => event.key === 'Enter' && runSearch()}
      />
      <button class="button" on:click={runSearch}>Search</button>
      {#if filtered}
        <button class="button ghost" on:click={clearFilters}>Clear</button>
      {/if}
    </div>
    {#if models.length}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Provider</th>
              <th>Reason</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {#each models as item}
              <tr>
                <td data-label="Model"><strong class="mono">{text(item.model)}</strong></td>
                <td data-label="Provider">
                  {names.get(text(item.prefix, '')) ?? text(item.prefix)}
                </td>
                <td data-label="Reason">
                  <span class="status warning">{reasonLabel(item.reason)}</span>
                </td>
                <td data-label="Source">
                  {sourceLabels[text(item.source, '')] ?? text(item.source)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <EmptyState
        icon="layers"
        title="No matching models"
        message="Try a different provider, reason, or search."
      />
    {/if}
    <Pagination {data} {navigateQuery} label="models" />
  </section>
{/if}

<style>
  .soon-panel,
  .group-grid {
    margin-bottom: 16px;
  }

  .soon-list,
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .soon-list .status {
    text-transform: none;
  }

  .group-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
    gap: 12px;
  }

  .group-card {
    display: grid;
    align-content: start;
    gap: 12px;
    padding: 16px;
  }

  .group-card header {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 8px;
  }

  .group-card h3 {
    margin: 0;
    font-size: 14px;
  }

  .group-card small {
    color: var(--muted);
    font-size: 11px;
  }

  .group-count {
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    white-space: nowrap;
  }

  .chips .status {
    text-transform: none;
  }

  .samples {
    display: grid;
    gap: 3px;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 11px;
    color: var(--muted);
  }

  .samples li {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .group-card .button {
    justify-self: start;
  }

  .model-filters {
    padding: 0 20px;
    margin-top: 16px;
  }
</style>
