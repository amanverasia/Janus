<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import { bool, compact, firstList, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  $: overview = object(data.overview);
  $: live = object(data.routing_live ?? data.live);
  $: settings = object(data.settings);
  $: providers = firstList(overview, 'providers');
  $: accounts = providers.flatMap((provider) =>
    firstList(provider, 'accounts').map((account): JsonObject => ({
      ...account,
      provider_id: provider.id,
      prefix: provider.prefix,
      quota: provider.quota
    }))
  );
  $: cooldowns = accounts.filter((account) => bool(account.cooldown_active));
  $: readyAccounts = accounts.filter(
    (account) => !bool(account.cooldown_active) && !bool(account.quota_deprioritized)
  );
  $: strategy = text(settings.account_strategy ?? live.account_strategy, 'round_robin')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

  function accountName(account: JsonObject): string {
    return text(
      account.key_label ?? account.key_masked ?? account.config_id ?? account.account_id,
      'Unnamed account'
    );
  }

  function cooldownRemaining(account: JsonObject): string {
    const seconds = Math.max(0, Number(account.cooldown_seconds ?? 0));
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    return `${Math.ceil(seconds / 60)}m`;
  }
</script>

<PageHeader
  title="Routing"
  description="See how Janus distributes attempts, applies cooldowns, and protects upstream capacity."
>
  <button
    class="button"
    disabled={!cooldowns.length}
    on:click={() =>
      action('/dashboard/api/routing/cooldowns/clear', {
        success: 'All cooldowns cleared'
      })}
  >
    <Icon name="refresh" />Clear cooldowns
  </button>
</PageHeader>

<div class="stats-grid">
  <StatCard label="Providers" value={compact(providers.length)} detail="Enabled routing groups" />
  <StatCard
    label="Ready accounts"
    value={compact(readyAccounts.length)}
    detail={`${accounts.length} configured`}
    tone="teal"
  />
  <StatCard
    label="Cooling down"
    value={compact(cooldowns.length || overview.cooldown_count)}
    tone="amber"
  />
  <StatCard label="Strategy" value={strategy} tone="violet" />
</div>

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
      <EmptyState
        icon="route"
        title="No active cooldowns"
        message="All configured accounts are eligible for routing."
      />
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
      {#each accounts.slice(0, 10) as account}
        <article class="account-row">
          <span class="account-order">{text(account.order, '—')}</span>
          <div>
            <strong>{accountName(account)}</strong>
            <small>
              {text(account.prefix ?? account.provider_id)} · {text(account.source, 'config')}
            </small>
          </div>
          {#if bool(account.cooldown_active)}
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
  </section>
</div>

<style>
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
    font-size: 9px;
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
    font-size: 9px;
  }
</style>
