<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import MiniChart from '$lib/components/MiniChart.svelte';
  import DataTable from '$lib/components/DataTable.svelte';
  import { compact, firstList, money, number, object, percent, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  export let navigateQuery: (params: Record<string, string>) => void;
  $: summary = object(data.summary);
  $: breakdown = firstList(data, 'breakdown', 'items');
  $: success = object(data.success);
  $: daily = firstList(summary, 'daily', 'series');
  $: days = text(data.days, '30');
  $: dimension = text(data.dimension, 'model');
  const columns = [
    {
      key: 'name',
      label: 'Dimension',
      format: (v: unknown, r: JsonObject) =>
        text(v ?? r.model ?? r.provider ?? r.account ?? r.client_key)
    },
    { key: 'requests', label: 'Requests', format: compact },
    {
      key: 'tokens',
      label: 'Tokens',
      format: (v: unknown, r: JsonObject) =>
        compact(v ?? number(r.input_tokens) + number(r.output_tokens))
    },
    { key: 'cost', label: 'Cost', format: money }
  ];
</script>

<PageHeader
  title="Analytics"
  description="Understand demand, reliability, and cost across every routing dimension."
>
  <select
    aria-label="Time range"
    value={days}
    on:change={(e) => navigateQuery({ days: (e.currentTarget as HTMLSelectElement).value })}
  >
    <option value="7">7 days</option>
    <option value="30">30 days</option>
    <option value="90">90 days</option>
    <option value="365">1 year</option>
  </select>
</PageHeader>
<div class="stats-grid">
  <StatCard label="Spend" value={money(summary.total_cost)} /><StatCard
    label="Requests"
    value={compact(summary.total_requests)}
    tone="teal"
  /><StatCard
    label="Tokens"
    value={compact(number(summary.total_input_tokens) + number(summary.total_output_tokens))}
    tone="violet"
  /><StatCard
    label="Success rate"
    value={percent(
      number(success.total) ? (number(success.success_2xx) / number(success.total)) * 100 : 0
    )}
    tone="amber"
  />
</div>
<div class="panel-grid">
  <section class="panel chart-card">
    <div class="panel-header">
      <div>
        <h2>Spend trajectory</h2>
        <p>Daily cost across the selected window</p>
      </div>
    </div>
    <div class="panel-body">
      <MiniChart
        values={daily.map((p) => number(p.cost ?? p.total_cost ?? p.value))}
        label="Daily spend"
      />
    </div>
  </section>
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Response health</h2>
        <p>HTTP outcome distribution</p>
      </div>
    </div>
    <div class="panel-body metric-list">
      {#each [['Successful', success.success_2xx, 'active'], ['Client errors', success.client_4xx, 'warning'], ['Server errors', success.server_5xx, 'error']] as row}<div
          class="metric-row"
        >
          <div>
            <strong>{row[0]}</strong>
            <span>{compact(row[1])}</span>
          </div>
          <div class="progress">
            <span
              style={`width:${number(success.total) ? Math.min(100, (number(row[1]) / number(success.total)) * 100) : 0}%`}
            ></span>
          </div>
        </div>{/each}
    </div>
  </section>
</div>
<section class="panel" style="margin-top:18px">
  <div class="panel-header">
    <div>
      <h2>Breakdown by {dimension}</h2>
      <p>Compare consumption and cost</p>
    </div>
    <div class="tabs">
      {#each ['model', 'provider', 'account', 'client_key'] as dim}<button
          class:active={dimension === dim}
          on:click={() => navigateQuery({ dimension: dim })}
        >
          {dim.replace('_', ' ')}
        </button>{/each}
    </div>
  </div>
  <DataTable rows={breakdown} {columns} emptyTitle="No analytics data" />
</section>
