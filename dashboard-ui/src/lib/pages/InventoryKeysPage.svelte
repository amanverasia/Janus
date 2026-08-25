<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { copyText } from '$lib/clipboard';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { dashboardFetch } from '$lib/api';
  import { compact, dateTime, firstList, idOf, list, money, number, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;
  export let navigateQuery: (params: Record<string, string>) => void;

  let providerId = '';
  let status = '';
  let search = '';
  let sort = 'credits';
  let direction = 'desc';
  let limit = 25;
  let offset = 0;
  let selected = new Set<string>();
  let detail: JsonObject | undefined;
  let detailOpen = false;
  let detailLoading = false;
  let detailPriority = 0;
  let revealedDetail = '';
  let revealBusy = false;
  let revealTimer = 0;
  let testResults: Record<string, string> = {};
  let testing = '';

  $: rows = firstList(data, 'keys', 'items');
  $: filters = object(data.filters);
  $: providers = list(filters.providers);
  $: statuses = Array.isArray(filters.statuses)
    ? filters.statuses.map((value) => text(value, '')).filter(Boolean)
    : [];
  $: pagination = object(data.pagination);
  $: total = number(
    pagination.total ?? data.total,
    offset + rows.length + (rows.length === limit ? 1 : 0)
  );
  $: page = number(pagination.page, Math.floor(offset / limit) + 1);
  $: totalPages = number(pagination.total_pages, Math.max(page, Math.ceil(total / limit)));
  $: allSelected = rows.length > 0 && rows.every((row) => selected.has(idOf(row)));
  $: detailModels = detail ? firstList(detail, 'models') : [];
  $: detailHistory = detail ? firstList(detail, 'history') : [];

  const tabs = [
    { label: 'Overview', href: '/dashboard/ui/inventory' },
    { label: 'All keys', href: '/dashboard/ui/inventory/keys' },
    { label: 'Add keys', href: '/dashboard/ui/inventory/add' },
    { label: 'Import JSON', href: '/dashboard/ui/inventory/import' }
  ];

  onMount(() => {
    const query = new URLSearchParams(window.location.search);
    providerId = query.get('provider_id') ?? '';
    status = query.get('status') ?? '';
    search = query.get('search') ?? '';
    sort = query.get('sort') ?? 'credits';
    direction = query.get('dir') ?? query.get('direction') ?? 'desc';
    limit = Math.max(1, number(query.get('limit'), 25));
    offset = Math.max(0, number(query.get('offset'), 0));
  });

  function applyFilters(nextOffset = 0) {
    selected = new Set();
    navigateQuery({
      provider_id: providerId,
      status,
      search,
      sort,
      dir: direction,
      limit: String(limit),
      offset: String(nextOffset)
    });
  }

  function clearFilters() {
    providerId = '';
    status = '';
    search = '';
    sort = 'credits';
    direction = 'desc';
    applyFilters();
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  function toggleAll() {
    selected = allSelected ? new Set() : new Set(rows.map(idOf).filter(Boolean));
  }

  async function bulk(kind: 'archive' | 'restore' | 'recheck' | 'delete') {
    if (!selected.size) return;
    if (
      kind === 'delete' &&
      !confirm(`Delete ${selected.size} selected credentials? This cannot be undone.`)
    )
      return;
    const body = new FormData();
    body.set('key_ids', [...selected].join(','));
    body.set('provider_id', providerId);
    body.set('status', status);
    body.set('search', search);
    body.set('sort', sort);
    body.set('dir', direction);
    body.set('limit', String(limit));
    body.set('offset', String(offset));
    if (kind === 'archive' || kind === 'restore') body.set('action', kind);
    const endpoint = kind === 'archive' || kind === 'restore' ? 'archive' : kind;
    await action(`/dashboard/api/inventory/keys/bulk/${endpoint}`, {
      body,
      success: `${selected.size} credentials ${kind === 'recheck' ? 'queued' : `${kind}d`}`
    });
    selected = new Set();
  }

  async function testKey(row: JsonObject) {
    const id = idOf(row);
    if (!id) return;
    testing = id;
    try {
      const result = object(
        await action(`/dashboard/api/inventory/keys/${encodeURIComponent(id)}/test`, {
          success: 'Credential test completed',
          refresh: false
        })
      );
      testResults = {
        ...testResults,
        [id]: text(result.message, result.ok ? 'Credential is valid' : 'Test completed')
      };
    } finally {
      testing = '';
    }
  }

  async function openDetail(row: JsonObject) {
    const id = idOf(row);
    if (!id) return;
    detailOpen = true;
    detailLoading = true;
    detail = undefined;
    clearReveal();
    try {
      const response = await dashboardFetch(
        `/dashboard/api/inventory/keys/${encodeURIComponent(id)}`,
        {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' }
        }
      );
      if (!response.ok) throw new Error('Credential detail unavailable');
      detail = object(await response.json());
      detailPriority = number(detail.priority);
    } finally {
      detailLoading = false;
    }
  }

  async function revealDetail() {
    if (!detail) return;
    if (revealedDetail) {
      clearReveal();
      return;
    }
    revealBusy = true;
    try {
      const response = await dashboardFetch(
        `/dashboard/api/inventory/keys/${encodeURIComponent(idOf(detail))}/reveal`,
        {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' }
        }
      );
      if (!response.ok) throw new Error('Credential unavailable');
      revealedDetail = text(object(await response.json()).key_value, '');
      window.clearTimeout(revealTimer);
      revealTimer = window.setTimeout(clearReveal, 30_000);
    } finally {
      revealBusy = false;
    }
  }

  function clearReveal() {
    window.clearTimeout(revealTimer);
    revealedDetail = '';
  }

  function closeDetail() {
    clearReveal();
    detailOpen = false;
  }

  async function copyRevealedDetail() {
    if (revealedDetail) await copyText(revealedDetail);
  }

  async function savePriority() {
    if (!detail) return;
    const body = new FormData();
    body.set('priority', String(Math.max(0, detailPriority)));
    await action(`/dashboard/api/inventory/keys/${encodeURIComponent(idOf(detail))}/priority`, {
      body,
      success: 'Routing priority updated'
    });
    detail = { ...detail, priority: Math.max(0, detailPriority) };
  }

  onDestroy(clearReveal);
</script>

<PageHeader
  title="Upstream credentials"
  description="Search, validate, and manage every account Janus can use for upstream routing."
>
  <a class="button" href="/dashboard/api/inventory/export" download>
    <Icon name="download" />Export
  </a>
  <button
    class="button"
    on:click={() =>
      action('/dashboard/api/inventory/recheck-all', { success: 'Inventory recheck started' })}
  >
    <Icon name="refresh" />Recheck all
  </button>
  <button class="button primary" on:click={() => navigate('/dashboard/ui/inventory/add')}>
    <Icon name="plus" />Add keys
  </button>
</PageHeader>

<nav class="inventory-tabs" aria-label="Credential inventory sections">
  {#each tabs as tab}<button
      class:active={tab.label === 'All keys'}
      on:click={() => navigate(tab.href)}
    >
      {tab.label}
    </button>{/each}
</nav>

{#if data.has_pending}
  <div class="validation-banner">
    <span class="validation-pulse"></span>
    <div>
      <strong>Validation in progress</strong>
      <p>New credentials are being checked. Refresh to retrieve the latest results.</p>
    </div>
    <button class="button" on:click={() => applyFilters(offset)}>Refresh</button>
  </div>
{/if}

<section class="panel inventory-table-panel">
  <form class="inventory-toolbar" on:submit|preventDefault={() => applyFilters()}>
    <label class="search-field">
      <Icon name="search" size={16} />
      <span class="sr-only">Search credentials</span>
      <input bind:value={search} placeholder="Search label, key, or node…" />
    </label>
    <label>
      <span class="sr-only">Provider</span>
      <select bind:value={providerId}>
        <option value="">All providers</option>
        {#each providers as provider}<option value={text(provider.id ?? provider.provider_id, '')}>
            {text(provider.display_name ?? provider.name ?? provider.id)}
          </option>{/each}
      </select>
    </label>
    <label>
      <span class="sr-only">Status</span>
      <select bind:value={status}>
        <option value="">All statuses</option>
        {#each statuses as item}<option value={item}>{item.replaceAll('_', ' ')}</option>{/each}
      </select>
    </label>
    <label>
      <span class="sr-only">Sort</span>
      <select bind:value={sort}>
        <option value="credits">Sort: credits</option>
        <option value="provider">Sort: provider</option>
        <option value="priority">Sort: priority</option>
        <option value="status">Sort: status</option>
        <option value="last_checked">Sort: last checked</option>
      </select>
    </label>
    <button
      type="button"
      class="direction-button"
      title={direction === 'desc' ? 'Descending' : 'Ascending'}
      on:click={() => {
        direction = direction === 'desc' ? 'asc' : 'desc';
        applyFilters();
      }}
    >
      {direction === 'desc' ? '↓' : '↑'}
    </button>
    <button class="button primary">Apply</button>
    {#if providerId || status || search}<button
        type="button"
        class="button ghost"
        on:click={clearFilters}
      >
        Clear
      </button>{/if}
  </form>

  {#if selected.size}
    <div class="bulk-bar">
      <strong>{selected.size} selected</strong>
      <span></span>
      <button class="button" on:click={() => bulk('recheck')}>
        <Icon name="refresh" size={14} />Recheck
      </button>
      {#if status === 'archived'}<button class="button" on:click={() => bulk('restore')}>
          Restore
        </button>{:else}<button class="button" on:click={() => bulk('archive')}>
          Archive
        </button>{/if}
      <button class="button danger" on:click={() => bulk('delete')}>
        <Icon name="trash" size={14} />Delete
      </button>
      <button
        class="icon-button"
        aria-label="Clear selection"
        on:click={() => (selected = new Set())}
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  {/if}

  {#if rows.length}
    <div class="table-wrap">
      <table class="inventory-table">
        <thead>
          <tr>
            <th class="select-cell">
              <input
                type="checkbox"
                checked={allSelected}
                aria-label="Select all credentials"
                on:change={toggleAll}
              />
            </th>
            <th>Provider</th>
            <th>Credential</th>
            <th>Status</th>
            <th>Credits</th>
            <th>Priority</th>
            <th>Checked</th>
            <th class="actions-heading"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row}
            {@const id = idOf(row)}
            <tr class:pending-row={text(row.status) === 'pending_validation'}>
              <td class="select-cell" data-label="Select">
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  aria-label={`Select ${text(row.key_masked, 'credential')}`}
                  on:change={() => toggle(id)}
                />
              </td>
              <td data-label="Provider">
                <div class="provider-cell">
                  <span>
                    {text(row.provider_display_name ?? row.provider_id, '?')
                      .slice(0, 2)
                      .toUpperCase()}
                  </span>
                  <div>
                    <strong>{text(row.provider_display_name ?? row.provider_id)}</strong>
                    {#if row.source_node}<small>{text(row.source_node)}</small>{/if}
                  </div>
                </div>
              </td>
              <td data-label="Credential">
                <div class="credential-cell">
                  <code>{text(row.key_masked)}</code>
                  <small>{text(row.key_label, 'Unlabelled credential')}</small>
                  {#if testResults[id]}<em>{testResults[id]}</em>{/if}
                </div>
              </td>
              <td data-label="Status">
                <span class="status {text(row.status, 'pending')}">
                  {text(row.status).replaceAll('_', ' ')}
                </span>
                {#if row.usability_status && row.usability_status !== 'unknown'}<small
                    class="cell-subtitle"
                  >
                    {text(row.usability_status).replaceAll('_', ' ')}
                  </small>{/if}
              </td>
              <td data-label="Credits">
                <strong class="credit-amount">
                  {row.credits_remaining == null ? '—' : money(row.credits_remaining)}
                </strong>
                {#if row.rate_limit_rpm != null}<small class="cell-subtitle">
                    {compact(row.rate_limit_rpm)} RPM
                  </small>{/if}
              </td>
              <td data-label="Priority">
                <span class="priority-pill">P{compact(row.priority)}</span>
              </td>
              <td data-label="Checked" class="muted checked-cell">
                {dateTime(row.last_checked_at)}
              </td>
              <td class="row-actions">
                <button class="icon-button" title="Inspect" on:click={() => openDetail(row)}>
                  <Icon name="eye" size={15} />
                </button>
                <button
                  class="icon-button"
                  class:spinning={testing === id}
                  title="Test"
                  disabled={testing === id}
                  on:click={() => testKey(row)}
                >
                  <Icon name="pulse" size={15} />
                </button>
                <button
                  class="icon-button"
                  title="Recheck"
                  on:click={() =>
                    action(`/dashboard/api/inventory/keys/${encodeURIComponent(id)}/recheck`, {
                      success: 'Recheck started'
                    })}
                >
                  <Icon name="refresh" size={15} />
                </button>
                {#if row.is_archived}<button
                    class="icon-button restore-button"
                    title="Restore"
                    on:click={() =>
                      action(`/dashboard/api/inventory/keys/${encodeURIComponent(id)}/restore`, {
                        success: 'Credential restored'
                      })}
                  >
                    <Icon name="check" size={15} />
                  </button>{:else}<button
                    class="icon-button archive-button"
                    title="Archive"
                    on:click={() =>
                      action(`/dashboard/api/inventory/keys/${encodeURIComponent(id)}/archive`, {
                        success: 'Credential archived'
                      })}
                  >
                    <Icon name="download" size={15} />
                  </button>{/if}
                <button
                  class="icon-button delete-button"
                  title="Delete"
                  on:click={() =>
                    confirm('Delete this credential? This cannot be undone.') &&
                    action(`/dashboard/api/inventory/keys/${encodeURIComponent(id)}`, {
                      method: 'DELETE',
                      success: 'Credential deleted'
                    })}
                >
                  <Icon name="trash" size={15} />
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <footer class="pagination">
      <p>
        Showing {offset + 1}–{Math.min(
          offset + rows.length,
          total
        )}{#if data.total != null || pagination.total != null}
          of {compact(total)}{/if}
      </p>
      <div>
        <label>
          Rows <select bind:value={limit} on:change={() => applyFilters()}>
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <button
          class="button"
          disabled={offset <= 0}
          on:click={() => applyFilters(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <span>
          Page {page}{#if totalPages > 1}
            / {totalPages}{/if}
        </span>
        <button
          class="button"
          disabled={rows.length < limit || (data.total != null && offset + limit >= total)}
          on:click={() => applyFilters(offset + limit)}
        >
          Next
        </button>
      </div>
    </footer>
  {:else}
    <EmptyState
      icon="key"
      title="No credentials match"
      message="Change the current filters or add a new upstream account."
    >
      <button class="button primary" on:click={() => navigate('/dashboard/ui/inventory/add')}>
        Add credentials
      </button>
    </EmptyState>
  {/if}
</section>

<Modal
  open={detailOpen}
  title="Credential details"
  description="Secrets remain masked until you explicitly reveal them."
  wide
  on:close={closeDetail}
>
  {#if detailLoading}<div class="detail-loading">
      <span class="loader"></span>
      <p>Loading credential health…</p>
    </div>
  {:else if detail}
    <div class="detail-hero">
      <span class="provider-avatar">
        {text(detail.provider_display_name ?? detail.provider_id, '?')
          .slice(0, 2)
          .toUpperCase()}
      </span>
      <div>
        <span class="eyebrow">{text(detail.provider_display_name ?? detail.provider_id)}</span>
        <h3>{text(detail.key_label, 'Unlabelled credential')}</h3>
        <code>{revealedDetail || text(detail.key_masked)}</code>
      </div>
      <span class="status {text(detail.status, 'pending')}">
        {text(detail.status).replaceAll('_', ' ')}
      </span>
    </div>
    <div class="detail-actions">
      <button class="button" disabled={revealBusy} on:click={revealDetail}>
        <Icon name="eye" size={15} />{revealedDetail
          ? 'Hide credential'
          : revealBusy
            ? 'Revealing…'
            : 'Reveal for 30s'}
      </button>
      <button class="button" disabled={!revealedDetail} on:click={copyRevealedDetail}>
        <Icon name="download" size={15} />Copy
      </button>
      <a
        class="button"
        href={`/dashboard/api/inventory/keys/${encodeURIComponent(idOf(detail))}/json`}
        download
      >
        Download JSON
      </a>
    </div>
    <div class="detail-metrics">
      <div>
        <span>Credits</span>
        <strong>{detail.credits_remaining == null ? '—' : money(detail.credits_remaining)}</strong>
      </div>
      <div>
        <span>Rate limit</span>
        <strong>
          {detail.rate_limit_rpm == null ? '—' : `${compact(detail.rate_limit_rpm)} RPM`}
        </strong>
      </div>
      <div>
        <span>Last checked</span>
        <strong>{dateTime(detail.last_checked_at)}</strong>
      </div>
    </div>
    <form class="priority-form" on:submit|preventDefault={savePriority}>
      <label class="field">
        <span>Routing priority</span>
        <input type="number" min="0" bind:value={detailPriority} />
        <small>Lower values are tried first within the provider.</small>
      </label>
      <button class="button">Save priority</button>
    </form>
    <div class="detail-section">
      <div class="section-title">
        <h3>Available models</h3>
        <span>{detailModels.length}</span>
      </div>
      {#if detailModels.length}<div class="model-cloud">
          {#each detailModels as model}<span>{text(model.model_id ?? model.name)}</span>{/each}
        </div>{:else}<p class="muted detail-copy">
          No model inventory has been reported for this credential.
        </p>{/if}
    </div>
    <div class="detail-section">
      <div class="section-title">
        <h3>Recent history</h3>
        <span>{detailHistory.length}</span>
      </div>
      {#if detailHistory.length}<div class="history-list">
          {#each detailHistory.slice(0, 6) as event}<div>
              <span class="status {text(event.new_status ?? event.status, 'pending')}">
                {text(event.new_status ?? event.status).replaceAll('_', ' ')}
              </span>
              <p>{text(event.previous_status, 'new')} → {text(event.new_status ?? event.status)}</p>
              <time>{dateTime(event.changed_at ?? event.created_at)}</time>
            </div>{/each}
        </div>{:else}<p class="muted detail-copy">No status history recorded yet.</p>{/if}
    </div>
  {:else}<EmptyState
      icon="vault"
      title="Credential unavailable"
      message="The detail response could not be loaded."
    />{/if}
</Modal>

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
  .validation-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 13px 16px;
    margin-bottom: 15px;
    border: 1px solid color-mix(in srgb, var(--warning) 25%, var(--line));
    border-radius: 14px;
    background: var(--warning-soft);
  }
  .validation-pulse {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--warning);
    box-shadow: 0 0 0 6px color-mix(in srgb, var(--warning) 13%, transparent);
    animation: pulse 1.5s ease infinite;
  }
  .validation-banner > div {
    flex: 1;
  }
  .validation-banner strong {
    font-size: 11px;
  }
  .validation-banner p {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 10px;
  }
  @keyframes pulse {
    50% {
      box-shadow: 0 0 0 10px transparent;
    }
  }
  .inventory-table-panel {
    overflow: visible;
  }
  .inventory-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 15px;
    border-bottom: 1px solid var(--line);
    flex-wrap: wrap;
  }
  .inventory-toolbar select,
  .inventory-toolbar input,
  .pagination select {
    min-height: 37px;
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-solid);
    color: var(--text);
    font-size: 11px;
  }
  .search-field {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 220px;
    flex: 1;
    padding: 0 10px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-solid);
    color: var(--muted);
  }
  .search-field input {
    width: 100%;
    padding-left: 0;
    border: 0;
    outline: 0;
    background: transparent;
  }
  .direction-button {
    width: 37px;
    height: 37px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-solid);
    color: var(--muted);
    cursor: pointer;
  }
  .bulk-bar {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 10px 15px;
    border-bottom: 1px solid var(--line);
    background: var(--accent-soft);
  }
  .bulk-bar strong {
    font-size: 11px;
    color: var(--accent-strong);
  }
  .bulk-bar > span {
    flex: 1;
  }
  .bulk-bar .button {
    min-height: 33px;
  }
  .bulk-bar .icon-button {
    width: 33px;
    height: 33px;
  }
  .inventory-table input[type='checkbox'] {
    accent-color: var(--accent);
  }
  .select-cell {
    width: 40px;
    text-align: center;
  }
  .pending-row {
    background: color-mix(in srgb, var(--warning-soft) 35%, transparent);
  }
  .provider-cell {
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .provider-cell > span,
  .provider-avatar {
    display: grid;
    place-items: center;
    width: 31px;
    height: 31px;
    flex: 0 0 auto;
    border-radius: 10px;
    color: var(--accent-strong);
    background: var(--accent-soft);
    font-size: 9px;
    font-weight: 800;
  }
  .provider-cell strong,
  .provider-cell small,
  .credential-cell code,
  .credential-cell small,
  .credential-cell em,
  .cell-subtitle {
    display: block;
  }
  .provider-cell strong {
    font-size: 11px;
  }
  .provider-cell small,
  .credential-cell small,
  .cell-subtitle {
    margin-top: 3px;
    color: var(--muted);
    font-size: 9px;
  }
  .credential-cell code {
    font:
      10px ui-monospace,
      monospace;
    color: var(--text);
  }
  .credential-cell em {
    max-width: 240px;
    margin-top: 4px;
    color: var(--accent-strong);
    font-size: 9px;
    font-style: normal;
  }
  .credit-amount {
    color: var(--success);
    font-size: 11px;
  }
  .priority-pill {
    display: inline-grid;
    place-items: center;
    min-width: 27px;
    height: 24px;
    padding: 0 6px;
    border-radius: 8px;
    background: var(--surface-soft);
    color: var(--muted);
    font:
      9px ui-monospace,
      monospace;
  }
  .checked-cell {
    font-size: 10px;
  }
  .row-actions {
    min-width: 184px;
  }
  .delete-button {
    color: var(--danger);
  }
  .archive-button {
    color: var(--warning);
  }
  .restore-button {
    color: var(--success);
  }
  .pagination {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 15px;
    padding: 13px 16px;
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 10px;
  }
  .pagination p {
    margin: 0;
  }
  .pagination > div {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .pagination label {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .pagination select {
    min-height: 34px;
  }
  .pagination .button {
    min-height: 34px;
  }
  .detail-loading {
    display: grid;
    place-items: center;
    min-height: 250px;
    color: var(--muted);
    font-size: 11px;
  }
  .detail-loading p {
    margin: 0;
  }
  .detail-hero {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-bottom: 17px;
    border-bottom: 1px solid var(--line);
  }
  .detail-hero .provider-avatar {
    width: 43px;
    height: 43px;
    border-radius: 13px;
  }
  .detail-hero > div {
    flex: 1;
    min-width: 0;
  }
  .detail-hero h3 {
    margin: 3px 0 5px;
    font-size: 16px;
  }
  .detail-hero code {
    display: block;
    max-width: 490px;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--muted);
    font:
      10px ui-monospace,
      monospace;
    white-space: nowrap;
  }
  .detail-actions {
    display: flex;
    gap: 7px;
    margin: 14px 0;
  }
  .detail-metrics {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 14px;
  }
  .detail-metrics > div {
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: var(--surface-soft);
  }
  .detail-metrics span,
  .detail-metrics strong {
    display: block;
  }
  .detail-metrics span {
    color: var(--muted);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .detail-metrics strong {
    margin-top: 5px;
    font-size: 11px;
  }
  .priority-form {
    display: flex;
    align-items: flex-end;
    gap: 9px;
    padding: 13px;
    border: 1px solid var(--line);
    border-radius: 12px;
  }
  .priority-form .field {
    flex: 1;
  }
  .priority-form .button {
    margin-bottom: 1px;
  }
  .detail-section {
    margin-top: 17px;
    padding-top: 16px;
    border-top: 1px solid var(--line);
  }
  .section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .section-title h3 {
    margin: 0;
    font-size: 12px;
  }
  .section-title span {
    display: grid;
    place-items: center;
    min-width: 23px;
    height: 21px;
    border-radius: 7px;
    color: var(--muted);
    background: var(--surface-soft);
    font-size: 9px;
  }
  .model-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
  .model-cloud span {
    padding: 5px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface-soft);
    font:
      9px ui-monospace,
      monospace;
  }
  .detail-copy {
    margin: 9px 0 0;
    font-size: 10px;
  }
  .history-list {
    display: grid;
    margin-top: 8px;
  }
  .history-list > div {
    display: grid;
    grid-template-columns: 120px 1fr auto;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
  }
  .history-list > div:last-child {
    border-bottom: 0;
  }
  .history-list p,
  .history-list time {
    margin: 0;
    color: var(--muted);
    font-size: 9px;
  }
  .history-list time {
    text-align: right;
  }
  @media (max-width: 900px) {
    .inventory-toolbar .search-field {
      min-width: 100%;
      order: -1;
    }
    .pagination {
      align-items: flex-start;
    }
    .pagination > div {
      flex-wrap: wrap;
      justify-content: flex-end;
    }
  }
  @media (max-width: 570px) {
    .inventory-tabs {
      width: 100%;
      overflow-x: auto;
    }
    .inventory-toolbar label:not(.search-field) {
      flex: 1;
    }
    .inventory-toolbar select {
      width: 100%;
    }
    .direction-button {
      flex: 0 0 auto;
    }
    .bulk-bar {
      overflow-x: auto;
    }
    .bulk-bar > span {
      display: none;
    }
    .bulk-bar strong {
      white-space: nowrap;
    }
    .inventory-table td.select-cell {
      justify-content: flex-end;
    }
    .inventory-table td.select-cell::before {
      display: block;
    }
    .row-actions {
      min-width: 0;
    }
    .pagination {
      display: block;
    }
    .pagination > div {
      margin-top: 10px;
      justify-content: flex-start;
    }
    .detail-hero {
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .detail-hero > div {
      min-width: calc(100% - 60px);
    }
    .detail-hero > .status {
      margin-left: 55px;
    }
    .detail-actions {
      flex-wrap: wrap;
    }
    .detail-metrics {
      grid-template-columns: 1fr;
    }
    .history-list > div {
      grid-template-columns: 105px 1fr;
    }
    .history-list time {
      grid-column: 2;
      text-align: left;
    }
  }
</style>
