<script lang="ts">
  import DataTable from '$lib/components/DataTable.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import Pagination from '$lib/components/Pagination.svelte';
  import { compact, firstList, number, rate, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigateQuery: (params: Record<string, string>) => void;

  let open = false;
  let tab = 'overrides';
  $: overrides = firstList(data, 'overrides');
  $: builtin = firstList(data, 'builtin');
  $: catalog = firstList(data, 'catalog');
  $: unpriced = firstList(data, 'unpriced');
  $: rows =
    tab === 'builtin'
      ? builtin
      : tab === 'catalog'
        ? catalog
        : tab === 'unpriced'
          ? unpriced
          : overrides;

  // The catalog is paged server-side; it reached 4,828 rows in production.
  // `builtin` and `overrides` are small and arrive whole.
  $: catalogTotal = number(data.total, catalog.length);
  $: catalogSearch = text(data.search, '');
  $: onCatalogTab = tab === 'catalog';

  let searchInput = '';
  $: if (catalogSearch !== searchInput && !searchDirty) searchInput = catalogSearch;
  let searchDirty = false;

  function runSearch() {
    searchDirty = false;
    navigateQuery({ search: searchInput.trim(), offset: '' });
  }

  const cols = [
    { key: 'model', label: 'Model' },
    { key: 'input_per_mtok', label: 'Input / MTok', format: rate },
    { key: 'output_per_mtok', label: 'Output / MTok', format: rate },
    { key: 'cache_read_per_mtok', label: 'Cache read', format: rate }
  ];

  const unpricedCols = [
    { key: 'model', label: 'Model' },
    { key: 'requests', label: 'Requests', format: (value: unknown) => compact(value) },
    {
      key: 'input_tokens',
      label: 'Tokens',
      format: (value: unknown, row: JsonObject) =>
        compact(number(value) + number(row.output_tokens))
    }
  ];

  $: activeCols = tab === 'unpriced' ? unpricedCols : cols;

  async function submit(event: SubmitEvent) {
    try {
      await action('/dashboard/api/pricing', {
        body: new FormData(event.currentTarget as HTMLFormElement),
        success: 'Pricing override saved'
      });
    } catch {
      return;
    }
    open = false;
  }
</script>

<PageHeader
  title="Pricing"
  description="Calculate gateway spend with layered model pricing and explicit overrides."
>
  <button
    class="button"
    on:click={() => action('/dashboard/api/pricing/sync', { success: 'Pricing catalog synced' })}
  >
    <Icon name="refresh" />Sync catalog
  </button>
  <button class="button primary" on:click={() => (open = true)}>
    <Icon name="plus" />Add override
  </button>
</PageHeader>
<section class="panel">
  <div class="panel-header">
    <div>
      <h2>Model rates</h2>
      <p>{text(data.catalog_count, '0')} synchronized catalog entries</p>
    </div>
    <div class="tabs">
      {#each ['overrides', 'unpriced', 'builtin', 'catalog'] as item}<button
          class:active={tab === item}
          on:click={() => (tab = item)}
        >
          {item}{item === 'unpriced' && unpriced.length ? ` (${unpriced.length})` : ''}
        </button>{/each}
    </div>
  </div>
  {#if onCatalogTab}
    <div class="filterbar" style="padding:0 20px;margin-top:16px">
      <input
        type="search"
        placeholder="Search the catalog by model…"
        bind:value={searchInput}
        on:input={() => (searchDirty = true)}
        on:keydown={(event) => event.key === 'Enter' && runSearch()}
        aria-label="Search pricing catalog"
      />
      <button class="button" on:click={runSearch}>Search</button>
      {#if catalogSearch}
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
      <span class="muted" style="margin-left:auto;font-size:11px">
        {compact(catalogTotal)} models
      </span>
    </div>
  {/if}
  <DataTable {rows} columns={activeCols} emptyTitle={`No ${tab} pricing`}>
    <svelte:fragment slot="actions" let:row>
      {#if tab === 'overrides'}<button
          class="icon-button"
          title="Delete override"
          on:click={() =>
            confirm('Delete this pricing override?') &&
            action(`/dashboard/api/pricing/${encodeURIComponent(text(row.model))}`, {
              method: 'DELETE',
              success: 'Pricing override deleted'
            })}
        >
          <Icon name="trash" size={15} />
        </button>{/if}
    </svelte:fragment>
  </DataTable>
  {#if onCatalogTab}
    <Pagination {data} {navigateQuery} label="models" />
  {/if}
</section>
<Modal {open} title="Add pricing override" on:close={() => (open = false)}>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field full">
        <span>Model ID or prefix</span>
        <input name="model" required placeholder="openai/gpt-5" />
      </label>
      <label class="field">
        <span>Input / MTok</span>
        <input name="input_per_mtok" type="number" min="0" step="0.0001" required />
      </label>
      <label class="field">
        <span>Output / MTok</span>
        <input name="output_per_mtok" type="number" min="0" step="0.0001" required />
      </label>
      <label class="field">
        <span>Cache creation / MTok</span>
        <input name="cache_creation_per_mtok" type="number" min="0" step="0.0001" value="0" />
      </label>
      <label class="field">
        <span>Cache read / MTok</span>
        <input name="cache_read_per_mtok" type="number" min="0" step="0.0001" value="0" />
      </label>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">Save override</button>
    </div>
  </form>
</Modal>
