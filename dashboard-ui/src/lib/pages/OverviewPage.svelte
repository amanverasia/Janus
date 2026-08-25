<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import MiniChart from '$lib/components/MiniChart.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { bool, compact, firstList, list, money, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  export let navigate: (href: string) => void;
  $: stats = object(data.stats ?? data.summary);
  $: daily = firstList(stats, 'daily', 'series').length
    ? firstList(stats, 'daily', 'series')
    : firstList(data, 'daily', 'series');
  $: chart = daily.map((point) => number(point.requests ?? point.total_requests ?? point.value));
  $: providers = firstList(data, 'providers', 'provider_health');
  $: checklist = object(data.setup_checklist);
</script>

<PageHeader
  title="Good to see you."
  description="A clear view of traffic, spend, and routing health across your AI gateway."
>
  <button class="button" on:click={() => navigate('/dashboard/ui/usage')}>
    <Icon name="pulse" />Live traffic
  </button>
  <button class="button primary" on:click={() => navigate('/dashboard/ui/providers')}>
    <Icon name="plus" />Add provider
  </button>
</PageHeader>
<div class="stats-grid">
  <StatCard
    label="Total requests"
    value={compact(stats.total_requests ?? data.total_requests)}
    detail="Across the selected period"
  />
  <StatCard
    label="Tokens routed"
    value={compact(
      number(stats.input_tokens ?? stats.total_input_tokens) +
        number(stats.output_tokens ?? stats.total_output_tokens)
    )}
    detail="Input and output combined"
    tone="teal"
  />
  <StatCard
    label="Spend today"
    value={money(data.today_cost ?? stats.total_cost)}
    detail="Configured reporting day"
    tone="violet"
  />
  <StatCard
    label="In flight"
    value={compact(data.live_inflight ?? stats.inflight)}
    detail={`${number(data.cooldown_count)} accounts cooling down`}
    tone="amber"
  />
</div>
<div class="panel-grid">
  <section class="panel chart-card">
    <div class="panel-header">
      <div>
        <h2>Request volume</h2>
        <p>Gateway activity over time</p>
      </div>
      <button class="button ghost" on:click={() => navigate('/dashboard/ui/analytics')}>
        Explore analytics <Icon name="arrow" size={14} />
      </button>
    </div>
    <div class="panel-body">
      <MiniChart values={chart} label="Request volume trend" />
      <div class="chart-legend">
        <span>{text(daily[0]?.date ?? daily[0]?.day, 'Earlier')}</span>
        <span>{text(daily[daily.length - 1]?.date ?? daily[daily.length - 1]?.day, 'Now')}</span>
      </div>
    </div>
  </section>
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Provider health</h2>
        <p>{number(data.provider_count ?? providers.length)} enabled</p>
      </div>
      <span class="status active">Operational</span>
    </div>
    <div class="panel-body">
      {#if providers.length}<div class="metric-list">
          {#each providers.slice(0, 6) as provider}<div class="metric-row">
              <div>
                <strong>{text(provider.name ?? provider.prefix ?? provider.id)}</strong>
                <span class="status {text(provider.status, 'active')}">
                  {text(provider.status, bool(provider.is_enabled, true) ? 'active' : 'disabled')}
                </span>
              </div>
              <div class="progress">
                <span
                  style={`width:${Math.min(100, number(provider.success_rate ?? provider.health_pct, 100))}%`}
                ></span>
              </div>
            </div>{/each}
        </div>
      {:else if number(data.provider_count) > 0}<div class="metric-list">
          <div class="metric-row">
            <div>
              <strong>Connected providers</strong>
              <span class="status active">{compact(data.provider_count)} available</span>
            </div>
            <div class="progress"><span style="width:100%"></span></div>
          </div>
          <div class="metric-row">
            <div>
              <strong>Account cooldowns</strong>
              <span class="status {number(data.cooldown_count) ? 'warning' : 'active'}">
                {number(data.cooldown_count) ? `${compact(data.cooldown_count)} active` : 'Clear'}
              </span>
            </div>
            <div class="progress">
              <span style={`width:${number(data.cooldown_count) ? 36 : 100}%`}></span>
            </div>
          </div>
        </div>
      {:else}<EmptyState
          icon="plug"
          title="No provider activity yet"
          message="Connect a provider to begin routing requests."
        />{/if}
    </div>
  </section>
</div>
{#if Object.keys(checklist).length}
  <section class="panel" style="margin-top:18px">
    <div class="panel-header">
      <div>
        <h2>Gateway readiness</h2>
        <p>Complete the essentials, then send your first request.</p>
      </div>
    </div>
    <div class="panel-body">
      <div class="cards-grid">
        <article class="item-card">
          <header>
            <h3>Connect a provider</h3>
            <span class="status {bool(checklist.has_providers) ? 'active' : 'pending'}">
              {bool(checklist.has_providers) ? 'Ready' : 'Next'}
            </span>
          </header>
          <p>Add credentials for at least one upstream model provider.</p>
        </article>
        <article class="item-card">
          <header>
            <h3>Create a client key</h3>
            <span class="status {bool(checklist.has_keys) ? 'active' : 'pending'}">
              {bool(checklist.has_keys) ? 'Ready' : 'Waiting'}
            </span>
          </header>
          <p>Issue a scoped Janus key for your application.</p>
        </article>
        <article class="item-card">
          <header>
            <h3>Route a request</h3>
            <span class="status {bool(checklist.has_requests) ? 'active' : 'pending'}">
              {bool(checklist.has_requests) ? 'Complete' : 'Waiting'}
            </span>
          </header>
          <p>Use the OpenAI-compatible endpoint to test the gateway.</p>
        </article>
      </div>
    </div>
  </section>
{/if}
