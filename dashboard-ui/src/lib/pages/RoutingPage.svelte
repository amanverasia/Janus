<script lang="ts">
  import EmptyState from '#lib/components/EmptyState.svelte';
  import Icon from '#lib/components/Icon.svelte';
  import PageHeader from '#lib/components/PageHeader.svelte';
  import Pagination from '#lib/components/Pagination.svelte';
  import StatCard from '#lib/components/StatCard.svelte';
  import {
    bool,
    compact,
    dateTime,
    duration,
    firstList,
    money,
    number,
    object,
    text
  } from '#lib/data.ts';
  import type { JsonObject, MutationOptions } from '#lib/types.ts';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigateQuery: (params: Record<string, string>) => void;
  export let navigate: (href: string) => void;

  $: overview = object(data.overview);
  $: live = object(data.routing_live ?? data.live);
  $: settings = object(data.settings);
  $: providers = firstList(overview, 'providers');
  // The routing pool is paged server-side; the overview ships slim provider
  // counts plus `total_accounts`/`ready_accounts` aggregates, and the
  // cooling-down subset arrives whole (it is small).
  $: accounts = firstList(data, 'accounts');
  $: cooldowns = firstList(data, 'cooldowns');
  $: unhealthy = firstList(data, 'unhealthy');
  $: auto = object(data.auto);
  $: autoStrategy = text(auto.strategy, 'balanced');
  $: autoModels = Array.isArray(auto.models)
    ? auto.models.filter((model): model is string => typeof model === 'string' && !!model.trim())
    : [];
  $: autoNote = `Top ${number(auto.top_n, 5)} model families with every eligible provider route (${autoModels.length} ${autoModels.length === 1 ? 'route' : 'routes'} in the chain). Signals are per provider route.`;
  $: autoPreview = firstList(auto, 'preview');
  $: autoOverrides = firstList(auto, 'overrides');
  let overrideModel = '';
  let overrideQuality = '0.9';
  let overrideSaving = false;

  async function saveOverride() {
    const model = overrideModel.trim();
    if (!model) return;
    autoSaving = true;
    overrideSaving = true;
    try {
      await action('/dashboard/api/v2/auto-overrides', {
        method: 'POST',
        body: { model, quality: Number(overrideQuality) },
        success: 'Override saved'
      });
      overrideModel = '';
    } finally {
      autoSaving = false;
      overrideSaving = false;
    }
  }

  async function removeOverride(model: string) {
    autoSaving = true;
    try {
      await action(`/dashboard/api/v2/auto-overrides/${encodeURIComponent(model)}`, {
        method: 'DELETE',
        success: 'Override removed'
      });
    } finally {
      autoSaving = false;
    }
  }
  let autoSaving = false;

  async function saveAutoStrategy(value: string) {
    autoSaving = true;
    try {
      const body = new FormData();
      body.set('key', 'auto_routing_strategy');
      body.set('value', value);
      await action('/dashboard/api/settings', { body, success: 'Auto strategy saved' });
    } finally {
      autoSaving = false;
    }
  }
  $: totalAccounts = number(overview.total_accounts, accounts.length);
  $: readyAccounts = number(overview.ready_accounts, accounts.length);
  $: unhealthyAccounts = number(overview.unhealthy_accounts, unhealthy.length);
  $: cooldownsOn = bool(settings.cooldowns_enabled ?? live.cooldowns_enabled, true);
  $: coolingCount = cooldowns.length || number(overview.cooldown_count, 0);
  $: coolingDetail = !cooldownsOn
    ? 'Cooldowns are disabled'
    : coolingCount
      ? 'Temporarily tried last'
      : 'None right now';
  $: strategyKey = text(settings.account_strategy ?? live.account_strategy, 'round_robin');
  $: strategyDetail =
    strategyKey === 'fill_first'
      ? 'Uses accounts in order'
      : strategyKey === 'sticky_rr'
        ? `Stays on one account for ${number(settings.sticky_limit, 3)} requests`
        : 'Rotates the first account per request';
  $: strategy = text(settings.account_strategy ?? live.account_strategy, 'round_robin')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

  function maskAccountLabel(label: unknown): string {
    const value = text(label, '');
    if (!value) return '';
    const match = value.match(/^([^@\s]+)@([^@\s]+\.[^@\s]+)$/);
    if (!match) return value;
    const local = match[1];
    const domain = match[2];
    const visible = local.slice(0, 1);
    return `${visible}***@${domain}`;
  }

  function accountName(account: JsonObject): string {
    const labeled = maskAccountLabel(account.key_label);
    return text(
      labeled || account.key_masked || account.config_id || account.account_id,
      'Unnamed account'
    );
  }

  function healthReason(account: JsonObject): string {
    const code = account.routing_health_status_code;
    const status = code === null || code === undefined ? '' : ` (HTTP ${text(code, '')})`;
    const reason = text(account.routing_health_reason, 'auth_error');
    const label =
      reason === 'payment_error'
        ? 'Payment required'
        : reason === 'auth_error'
          ? 'Authentication failed'
          : reason.replaceAll('_', ' ');
    return `${label}${status}`;
  }

  function cooldownRemaining(account: JsonObject): string {
    const seconds = Math.max(0, Number(account.cooldown_seconds ?? 0));
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    return `${Math.ceil(seconds / 60)}m`;
  }

  let clearing = false;

  async function clearCooldowns() {
    if (clearing) return;
    clearing = true;
    try {
      await action('/dashboard/api/routing/cooldowns/clear', {
        success: 'All cooldowns cleared'
      });
    } catch {
    } finally {
      clearing = false;
    }
  }
</script>

<PageHeader
  title="Routing health"
  description="See how Janus distributes attempts, applies cooldowns, and protects upstream capacity."
>
  <button class="button" disabled={!cooldowns.length || clearing} on:click={clearCooldowns}>
    <Icon name="refresh" />{clearing ? 'Clearing…' : 'Clear cooldowns'}
  </button>
</PageHeader>

{#if !bool(settings.cooldowns_enabled ?? live.cooldowns_enabled, true)}
  <div class="alert-strip warning" role="alert">
    <Icon name="warning" size={17} />
    <div>
      <strong>Account cooldowns are disabled</strong>
      <span>
        Failing accounts stay in rotation. Repeated authentication, payment, or rate-limit errors
        can make requests slow or exhaust every provider. Credentials that keep returning payment or
        authentication errors are still tried last.
        <a href="/dashboard/ui/settings">Enable account cooldowns in Settings</a>
        .
      </span>
    </div>
  </div>
{/if}

<div class="stats-grid">
  <StatCard label="Providers" value={compact(providers.length)} detail="Enabled routing groups" />
  <StatCard
    label="Ready accounts"
    value={compact(readyAccounts)}
    detail={unhealthyAccounts
      ? `${compact(totalAccounts)} configured · ${compact(unhealthyAccounts)} failing`
      : `${compact(totalAccounts)} configured`}
    tone="teal"
  />
  <StatCard
    label="Cooling down"
    value={compact(coolingCount)}
    detail={coolingDetail}
    tone="amber"
  />
  <StatCard label="Strategy" value={strategy} detail={strategyDetail} tone="violet" />
</div>

<section class="panel auto-card">
  <div class="panel-header">
    <div>
      <h2>Auto routing</h2>
      <p>
        Requests with model "auto" rank candidates per request and use the top chain as the fallback
        try-order
      </p>
    </div>
    <div class="field auto-strategy">
      <select
        aria-label="Auto routing strategy"
        disabled={autoSaving}
        value={autoStrategy}
        on:change={(event) => saveAutoStrategy((event.currentTarget as HTMLSelectElement).value)}
      >
        <option value="balanced">Balanced</option>
        <option value="cheapest">Cheapest</option>
        <option value="fastest">Fastest</option>
        <option value="quality">Quality</option>
      </select>
    </div>
  </div>
  <div class="panel-body auto-chain-body">
    {#if autoModels.length}
      <p class="auto-chain">
        <span class="mono">{text(auto.top, '')}</span>
        <span class="muted">would pick now</span>
        {#each autoModels as model, index}<span class="status mono">
            {index + 1}. {model}
          </span>{/each}
      </p>
      <p class="muted auto-note">{autoNote}</p>
    {:else}
      <p class="muted">No priced, routable models for auto yet.</p>
    {/if}
  </div>
  {#if autoPreview.length}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Score</th>
            <th>Cost/MTok</th>
            <th>Err %</th>
            <th>TPS</th>
            <th>TTFT</th>
            <th>Samples</th>
          </tr>
        </thead>
        <tbody>
          {#each autoPreview as row}
            <tr>
              <td data-label="Model">
                <strong class="mono">{text(row.model)}</strong>
                {#if bool(row.overridden)}<span class="status active">override</span>{/if}
              </td>
              <td data-label="Score">{number(row.score).toFixed(2)}</td>
              <td data-label="Cost">{money(row.blended_cost)}</td>
              <td data-label="Errors">
                {row.error_rate != null ? `${(number(row.error_rate) * 100).toFixed(0)}%` : '—'}
              </td>
              <td data-label="TPS">{row.tps_p50 != null ? number(row.tps_p50).toFixed(1) : '—'}</td>
              <td data-label="TTFT">
                {duration(row.ttft_p50_ms)}
              </td>
              <td data-label="Samples">
                {number(row.samples).toLocaleString()}
                {#if row.signal_scope === 'model'}<small class="muted scope-note">
                    model-wide
                  </small>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  <div class="panel-body override-body">
    <div class="override-row">
      <label class="field override-model">
        <span>Quality override</span>
        <input
          placeholder="model id, e.g. deepseek-chat"
          bind:value={overrideModel}
          aria-label="Override model"
        />
      </label>
      <label class="field override-quality">
        <span>Quality 0–1</span>
        <input
          type="number"
          min="0"
          max="1"
          step="0.05"
          bind:value={overrideQuality}
          aria-label="Override quality"
        />
      </label>
      <button
        class="button primary"
        disabled={!overrideModel.trim() || overrideSaving}
        on:click={saveOverride}
      >
        Save override
      </button>
    </div>
    {#if autoOverrides.length}
      <div class="chips">
        {#each autoOverrides as override (override.model)}
          <span class="status mono">
            {override.model} = {number(override.quality).toFixed(2)}
            <button
              class="link"
              aria-label={`Remove override for ${override.model}`}
              on:click={() => removeOverride(text(override.model))}
            >
              remove
            </button>
          </span>
        {/each}
      </div>
    {/if}
  </div>
</section>
<div class="panel-grid equal">
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Active cooldowns</h2>
        <p>Accounts temporarily moved down the try order</p>
      </div>
    </div>
    {#if cooldowns.length}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Account</th>
              <th>State</th>
              <th>Remaining</th>
            </tr>
          </thead>
          <tbody>
            {#each cooldowns as account}
              <tr>
                <td data-label="Provider">
                  <strong>{text(account.prefix ?? account.provider_id)}</strong>
                </td>
                <td data-label="Account">{accountName(account)}</td>
                <td data-label="State"><span class="status cooldown">Cooling down</span></td>
                <td data-label="Remaining">{cooldownRemaining(account)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="panel-empty muted">
        {cooldownsOn
          ? 'No accounts are cooling down. All configured accounts are eligible for routing.'
          : 'Cooldowns are disabled, so no account is being paused.'}
      </p>
    {/if}
    {#if unhealthy.length}
      <div class="panel-header failing-header">
        <div>
          <h2>Failing credentials</h2>
          <p>
            {compact(unhealthyAccounts)} repeatedly returned payment or authentication errors and are
            tried last
          </p>
        </div>
        <a
          class="button ghost"
          href="/dashboard/ui/inventory/keys?status=routing_unhealthy"
          on:click|preventDefault={() =>
            navigate('/dashboard/ui/inventory/keys?status=routing_unhealthy')}
        >
          Review in Inventory
        </a>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Account</th>
              <th>Reason</th>
              <th>Since</th>
            </tr>
          </thead>
          <tbody>
            {#each unhealthy as account}
              <tr>
                <td data-label="Provider">
                  <strong>{text(account.prefix ?? account.provider_id)}</strong>
                </td>
                <td data-label="Account">{accountName(account)}</td>
                <td data-label="Reason">
                  <span class="status error">{healthReason(account)}</span>
                </td>
                <td data-label="Since">{dateTime(account.routing_health_at)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if unhealthyAccounts > unhealthy.length}
        <p class="panel-empty muted">
          Showing {compact(unhealthy.length)} of {compact(unhealthyAccounts)}.
        </p>
      {/if}
    {/if}
  </section>

  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Routing pool</h2>
        <p>Account order and current eligibility</p>
      </div>
    </div>
    <div class="panel-body account-list">
      {#each accounts as account}
        <article class="account-row">
          <span class="account-order">{text(account.order, '—')}</span>
          <div>
            <strong>{accountName(account)}</strong>
            <small>
              {text(account.prefix ?? account.provider_id)} · {text(account.source, 'config')}
            </small>
          </div>
          {#if bool(account.routing_unhealthy)}
            <span class="status error" title={healthReason(account)}>Failing</span>
          {:else if bool(account.cooldown_active)}
            <span class="status cooldown">Cooling</span>
          {:else if bool(account.quota_deprioritized)}
            <span class="status pending">Quota low</span>
          {:else}
            <span class="status active">Ready</span>
          {/if}
        </article>
      {:else}
        <EmptyState
          icon="route"
          title="No routing accounts"
          message="Connect a provider or add inventory credentials to build the routing pool."
        />
      {/each}
    </div>
    <Pagination {data} {navigateQuery} label="accounts" />
  </section>
</div>

<style>
  .auto-card .status {
    text-transform: none;
  }

  .auto-strategy select {
    width: auto;
    min-width: 140px;
  }

  .auto-chain-body {
    display: grid;
    gap: 10px;
    padding-bottom: 12px;
  }

  .auto-chain-body p {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 0;
  }

  .auto-note {
    font-size: 12px;
  }

  .scope-note {
    margin-left: 6px;
    font-size: 11px;
  }

  .override-body {
    border-top: 1px solid var(--line);
  }

  .panel-empty {
    margin: 0;
    padding: var(--space-4) var(--space-5);
    font-size: 13px;
  }

  .failing-header {
    border-top: 1px solid var(--line);
  }

  .override-row {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 12px;
  }

  .override-model {
    flex: 1 1 240px;
  }

  .override-quality {
    flex: 0 1 140px;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 14px;
  }

  .account-list {
    display: grid;
    gap: 8px;
  }

  .account-row {
    display: grid;
    grid-template-columns: 30px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: var(--surface-soft);
  }

  .account-order {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 9px;
    color: var(--accent-strong);
    background: var(--accent-soft);
    font-size: 11px;
    font-weight: 800;
  }

  .account-row strong,
  .account-row small {
    display: block;
  }

  .account-row strong {
    overflow: hidden;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .account-row small {
    margin-top: 3px;
    color: var(--muted);
    font-size: 11px;
  }
</style>
