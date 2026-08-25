<script lang="ts">
  import DataTable from '$lib/components/DataTable.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { firstList, money, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  let open = false;
  let tab = 'overrides';
  $: overrides = firstList(data, 'overrides');
  $: builtin = firstList(data, 'builtin');
  $: catalog = firstList(data, 'catalog');
  $: rows = tab === 'builtin' ? builtin : tab === 'catalog' ? catalog : overrides;

  const cols = [
    { key: 'model', label: 'Model' },
    { key: 'input_per_mtok', label: 'Input / MTok', format: money },
    { key: 'output_per_mtok', label: 'Output / MTok', format: money },
    { key: 'cache_read_per_mtok', label: 'Cache read', format: money }
  ];

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
      {#each ['overrides', 'builtin', 'catalog'] as item}<button
          class:active={tab === item}
          on:click={() => (tab = item)}
        >
          {item}
        </button>{/each}
    </div>
  </div>
  <DataTable {rows} columns={cols} emptyTitle={`No ${tab} pricing`}>
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
