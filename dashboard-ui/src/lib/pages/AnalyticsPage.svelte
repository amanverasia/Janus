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
  $: savings = object(data.savings);
  $: savingsRows = firstList(savings, 'by_model');
  $: baseline = text(savings.baseline, 'gpt-4o');
  const baselineOptions = [
    { value: 'gpt-4o', label: 'GPT-4o' },
    { value: 'gpt-4o-mini', label: 'GPT-4o mini' },
    { value: 'gpt-4.1', label: 'GPT-4.1' },
    { value: 'o3', label: 'o3' },
    { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
    { value: 'claude-opus-4-20250514', label: 'Claude Opus 4' },
    { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { value: 'deepseek-chat', label: 'DeepSeek Chat' },
    { value: 'grok-4', label: 'Grok 4' }
  ];
  $: baselineChoices = baselineOptions.some((option) => option.value === baseline)
    ? baselineOptions
    : [{ value: baseline, label: baseline }, ...baselineOptions];
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
          <div class="progress {row[2]}">
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
      <h2>Savings vs baseline</h2>
      <p>
        What the last {number(days)} days of priced traffic would have cost on
        {baseline}
      </p>
    </div>
    <select
      aria-label="Baseline model"
      value={baseline}
      on:change={(e) => navigateQuery({ baseline: (e.currentTarget as HTMLSelectElement).value })}
    >
      {#each baselineChoices as option (option.value)}<option value={option.value}>
          {option.label}
        </option>{/each}
    </select>
  </div>
  {#if number(savings.baseline_cost) > 0}
    <div class="stats-grid savings-grid">
      <StatCard
        label="Saved"
        value={money(number(savings.savings))}
        detail={`${percent(number(savings.savings_pct))} of baseline`}
        tone="teal"
      />
      <StatCard label="Baseline cost" value={money(number(savings.baseline_cost))} />
      <StatCard label="Actual spend" value={money(number(savings.actual_cost))} />
      <StatCard
        label="Excluded"
        value={compact(
          number(object(savings.excluded).subscription_requests) +
            number(object(savings.excluded).unpriced_requests)
        )}
        detail="Subscription + unpriced requests"
        tone="amber"
      />
    </div>
    {#if savingsRows.length}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Requests</th>
              <th>Actual</th>
              <th>On {baseline}</th>
              <th>Saved</th>
            </tr>
          </thead>
          <tbody>
            {#each savingsRows as row}
              <tr>
                <td data-label="Model"><strong class="mono">{text(row.model)}</strong></td>
                <td data-label="Requests">{compact(row.requests)}</td>
                <td data-label="Actual">{money(row.actual_cost)}</td>
                <td data-label="On baseline">{money(row.baseline_cost)}</td>
                <td data-label="Saved">{money(row.savings)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {:else}
    <p class="savings-empty">
      No priced traffic to compare yet — requests on subscription or unpriced models are never
      counted as savings.
    </p>
  {/if}
</section>
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

<style>
  .savings-grid {
    padding: 0 20px;
    margin: 14px 0;
  }

  .savings-empty {
    margin: 0;
    padding: 16px 20px;
    color: var(--muted);
    font-size: 13px;
  }
</style>
