<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import MiniChart from '$lib/components/MiniChart.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { onDestroy } from 'svelte';
  import { copyText } from '$lib/clipboard';
  import { bool, compact, firstList, money, number, object, percent, text } from '$lib/data';
  import { curlSnippet } from '$lib/snippets';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  export let navigate: (href: string) => void;

  $: savingsToday =
    data.savings_today && typeof data.savings_today === 'object'
      ? object(data.savings_today)
      : null;
  $: stats = object(data.stats ?? data.summary);
  $: daily = firstList(stats, 'daily', 'series').length
    ? firstList(stats, 'daily', 'series')
    : firstList(data, 'daily', 'series');
  $: chart = daily.map((point) => number(point.requests ?? point.total_requests ?? point.value));
  $: providers = firstList(data, 'providers', 'provider_health');
  $: checklist = object(data.setup_checklist);
  // Once every step is done the panel is permanent noise on a working gateway.
  $: checklistComplete =
    bool(checklist.has_providers) && bool(checklist.has_keys) && bool(checklist.has_requests);
  $: hasChecklist = Object.keys(checklist).length > 0;
  $: firstRun = hasChecklist && !checklistComplete;
  $: base = text(data.base_url, `${location.origin}/v1`);
  $: curlCommand = curlSnippet(base, true);
  $: steps = [
    {
      title: 'Connect credentials',
      detail: 'Paste an API key or drop a CLI login file.',
      done: bool(checklist.has_providers),
      href: '/dashboard/ui/connect',
      cta: 'Open Connect'
    },
    {
      title: 'Create a client key',
      detail: 'Your apps use it to call Janus.',
      done: bool(checklist.has_keys),
      href: '/dashboard/ui/keys',
      cta: 'Create key'
    },
    {
      title: 'Send a first request',
      detail: 'Use the test request above with your new key.',
      done: bool(checklist.has_requests),
      href: '/dashboard/ui/tools',
      cta: 'More snippets'
    }
  ];
  $: currentStep = steps.findIndex((step) => !step.done);
  let copied = '';
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  async function copy(value: string, label: string) {
    try {
      await copyText(value);
      copied = label;
    } catch {
      copied = 'error';
    }
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => (copied = ''), 1800);
  }
  onDestroy(() => clearTimeout(copyTimer));
  $: providerCount = number(data.provider_count ?? providers.length);
  $: cooldowns = number(data.cooldown_count);
  $: providerHealthTone = providerCount === 0 ? 'pending' : cooldowns > 0 ? 'warning' : 'active';
  $: providerHealthLabel =
    providerCount === 0 ? 'No providers' : cooldowns > 0 ? 'Degraded' : 'Operational';
</script>

<PageHeader title="Overview">
  <button class="button" on:click={() => navigate('/dashboard/ui/usage')}>
    <Icon name="pulse" />Live usage
  </button>
  <button class="button primary" on:click={() => navigate('/dashboard/ui/connect')}>
    <Icon name="plus" />Connect credentials
  </button>
</PageHeader>
<section class="endpoint" class:first-run={firstRun} aria-labelledby="endpoint-heading">
  <div class="endpoint-address">
    <h2 id="endpoint-heading">
      {firstRun ? 'Point your apps at this address' : 'Your endpoint'}
    </h2>
    <p class="endpoint-url"><code>{base}</code></p>
    <div class="endpoint-actions">
      <button class="button" on:click={() => copy(base, 'url')}>
        <Icon name="copy" size={15} />{copied === 'url'
          ? 'Copied'
          : copied === 'error'
            ? 'Copy failed'
            : 'Copy address'}
      </button>
    </div>
    <details class="endpoint-test" open={firstRun && currentStep === 2}>
      <summary>Show a test request</summary>
      <pre>{curlCommand}</pre>
      <button class="button" on:click={() => copy(curlCommand, 'curl')}>
        <Icon name="copy" size={15} />{copied === 'curl' ? 'Copied' : 'Copy command'}
      </button>
    </details>
  </div>
  {#if firstRun}
    <ol class="setup-steps" aria-label="Get started">
      {#each steps as step, index (step.title)}<li
          class:done={step.done}
          class:current={index === currentStep}
        >
          <span class="step-marker" aria-hidden="true">
            {#if step.done}<Icon name="check" size={14} />{:else}{index + 1}{/if}
          </span>
          <div>
            <h3>{step.title}</h3>
            <p>{step.done ? 'Done' : step.detail}</p>
            {#if !step.done}<a
                class="button {index === currentStep ? 'primary' : ''}"
                href={step.href}
                on:click|preventDefault={() => navigate(step.href)}
              >
                {step.cta}
              </a>{/if}
          </div>
        </li>{/each}
    </ol>
  {/if}
</section>
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
  {#if savingsToday}
    <StatCard
      label="Saved today"
      value={money(number(savingsToday.savings))}
      detail={`${percent(number(savingsToday.savings_pct))} vs ${text(
        savingsToday.baseline,
        'baseline'
      )} · configured reporting day`}
      tone="teal"
    />
  {/if}
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
      <span class="status {providerHealthTone}">{providerHealthLabel}</span>
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

<style>
  .endpoint {
    margin-bottom: var(--space-5);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .endpoint-address {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: var(--space-2) var(--space-5);
    padding: var(--space-5) var(--space-5) var(--space-5) var(--space-5);
  }
  .endpoint h2 {
    grid-column: 1 / -1;
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
  }
  .endpoint-url {
    min-width: 0;
    margin: 0;
  }
  .endpoint-url code {
    display: block;
    overflow-wrap: anywhere;
    color: var(--text);
    font:
      500 clamp(18px, 2vw, 24px) / 1.2 ui-monospace,
      SFMono-Regular,
      Menlo,
      monospace;
    letter-spacing: -0.02em;
  }
  .first-run .endpoint-url code {
    font-size: clamp(22px, 3.4vw, 40px);
    letter-spacing: -0.035em;
  }
  .endpoint-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: flex-end;
    gap: var(--space-2);
  }
  .endpoint-test {
    grid-column: 1 / -1;
  }
  .endpoint-test summary {
    width: max-content;
    color: var(--accent-strong);
    font-size: 13px;
    font-weight: 620;
    cursor: pointer;
  }
  .endpoint-test > .button {
    margin-top: var(--space-1);
  }
  .endpoint-test pre {
    margin: var(--space-3) 0 var(--space-2);
    padding: var(--space-4);
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--surface-soft);
    overflow-x: auto;
    font:
      12px/1.6 ui-monospace,
      SFMono-Regular,
      Menlo,
      monospace;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .setup-steps {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--line);
    list-style: none;
  }
  .setup-steps li {
    display: flex;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-5) var(--space-5);
  }
  .setup-steps li + li {
    border-left: 1px solid var(--line);
  }
  .step-marker {
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    flex: 0 0 auto;
    border: 1.5px solid var(--line-strong);
    border-radius: 50%;
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .current .step-marker {
    border-color: var(--accent);
    color: var(--accent-strong);
  }
  .done .step-marker {
    border-color: var(--accent);
    background: var(--accent);
    color: var(--on-accent);
  }
  .setup-steps h3 {
    margin: 3px 0 2px;
    font-size: 14px;
  }
  .done h3 {
    color: var(--muted);
  }
  .setup-steps p {
    margin: 0 0 var(--space-3);
    color: var(--muted);
    font-size: 13px;
    line-height: 1.45;
  }
  @media (max-width: 900px) {
    .endpoint-address {
      grid-template-columns: 1fr;
    }
    .endpoint-actions {
      justify-content: flex-start;
    }
    .setup-steps {
      grid-template-columns: 1fr;
    }
    .setup-steps li + li {
      border-left: 0;
      border-top: 1px solid var(--line);
    }
  }
</style>
