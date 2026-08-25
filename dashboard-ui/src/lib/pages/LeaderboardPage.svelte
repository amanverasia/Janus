<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { compact, firstList, money, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  export let navigateQuery: (p: Record<string, string>) => void;
  $: rows = firstList(data, 'leaderboard', 'rows', 'items');
  $: sort = text(data.sort, 'tokens');
</script>

<PageHeader
  title="Leaderboard"
  description="The clients and models driving the most gateway activity."
>
  <div class="tabs">
    {#each ['tokens', 'cost', 'requests'] as item}<button
        class:active={sort === item}
        on:click={() => navigateQuery({ sort: item })}
      >
        {item}
      </button>{/each}
  </div>
</PageHeader>
<section class="panel">
  {#if rows.length}<div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Client</th>
            <th>Requests</th>
            <th>Tokens</th>
            <th>Success</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row, i}<tr>
              <td data-label="Rank"><strong>#{i + 1}</strong></td>
              <td data-label="Client">{text(row.client_key_name ?? row.key_name ?? row.name)}</td>
              <td data-label="Requests">{compact(row.requests)}</td>
              <td data-label="Tokens">{compact(row.tokens ?? row.total_tokens)}</td>
              <td data-label="Success">{Number(row.success_pct ?? 0).toFixed(1)}%</td>
              <td data-label="Cost">{money(row.cost ?? row.total_cost)}</td>
            </tr>{/each}
        </tbody>
      </table>
    </div>{:else}<EmptyState
      icon="trophy"
      title="The leaderboard is quiet"
      message="Rankings appear after Janus records usage."
    />{/if}
</section>
