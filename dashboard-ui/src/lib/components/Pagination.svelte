<script lang="ts">
  import { compact, number } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  // Server-side pagination controls. The state route merges `meta.pagination`
  // ({ total, limit, offset, ... }) onto `data`, so a page passes its `data`
  // and its `navigateQuery` helper; `navigateQuery` preserves other query
  // params (search, provider) because it merges into the current URL.
  export let data: JsonObject;
  export let navigateQuery: (params: Record<string, string>) => void;
  export let label = 'items';

  $: offset = number(data.offset);
  $: limit = number(data.limit, 25) || 25;
  $: total = number(data.total);
  $: page = Math.floor(offset / limit) + 1;
  $: pages = Math.max(1, Math.ceil(total / limit));

  function pageTo(nextOffset: number) {
    navigateQuery({
      offset: String(Math.max(0, nextOffset)),
      limit: String(limit)
    });
  }
</script>

{#if total > limit}
  <div class="pagination">
    <span class="pagination-summary">
      {compact(total)}
      {label} · page {page} of {pages}
    </span>
    <span class="pagination-controls">
      <button class="button" disabled={offset <= 0} on:click={() => pageTo(offset - limit)}>
        Previous
      </button>
      <button
        class="button"
        disabled={offset + limit >= total}
        on:click={() => pageTo(offset + limit)}
      >
        Next
      </button>
    </span>
  </div>
{/if}

<style>
  .pagination {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 12px 20px;
  }

  .pagination-summary {
    color: var(--muted);
    font-size: 11px;
  }

  .pagination-controls {
    display: inline-flex;
    gap: 8px;
  }
</style>
