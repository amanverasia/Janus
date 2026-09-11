<script lang="ts">
  import EmptyState from './EmptyState.svelte';
  import { idOf, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let rows: JsonObject[] = [];
  export let columns: {
    key: string;
    label: string;
    format?: (value: unknown, row: JsonObject) => string;
  }[] = [];
  export let emptyTitle = 'No records found';
  // Stable identity per row; falls back to index only when a row has no id.
  export let rowKey: (row: JsonObject, index: number) => string | number = (row, index) =>
    idOf(row) || index;
  $: hasActions = $$slots.actions;
</script>

{#if rows.length}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          {#each columns as column}<th>{column.label}</th>{/each}
          {#if hasActions}<th class="actions-heading"><span class="sr-only">Actions</span></th>{/if}
        </tr>
      </thead>
      <tbody>
        {#each rows as row, index (rowKey(row, index))}<tr>
            {#each columns as column}<td data-label={column.label}>
                {column.format ? column.format(row[column.key], row) : text(row[column.key])}
              </td>{/each}
            {#if hasActions}<td class="row-actions"><slot name="actions" {row} /></td>{/if}
          </tr>{/each}
      </tbody>
    </table>
  </div>
{:else}<EmptyState title={emptyTitle} message="Try changing filters, or add the first item." />{/if}
