<script lang="ts">
  import { onDestroy } from 'svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { copyText } from '$lib/clipboard';
  import Icon from '$lib/components/Icon.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import { dashboardFetch } from '$lib/api';
  import { compact, dateTime, firstList, idOf, money, number, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigate: (href: string) => void;

  let revealed: Record<string, string> = {};
  let revealTimers: Record<string, number> = {};
  let revealBusy = '';
  let copied = '';

  $: summary = object(data.summary);
  $: providers = firstList(data, 'provider_cards');
  $: credits = firstList(data, 'credit_summary');
  $: bestKeys = firstList(data, 'best_keys');
  $: activity = firstList(data, 'recent_activity');
  $: encryption = object(data.encryption);
  $: providerEncryption = object(data.provider_encryption);
  $: creditTotal = credits.reduce((total, row) => total + number(row.total_remaining), 0);
  $: encryptedCount = number(encryption.encrypted) + number(providerEncryption.encrypted);
  $: plaintextCount = number(encryption.plaintext) + number(providerEncryption.plaintext);

  const tabs = [
    { label: 'Overview', href: '/dashboard/ui/inventory' },
    { label: 'All keys', href: '/dashboard/ui/inventory/keys' },
    { label: 'Add keys', href: '/dashboard/ui/inventory/add' },
    { label: 'Import JSON', href: '/dashboard/ui/inventory/import' }
  ];

  async function reveal(key: JsonObject) {
    const id = idOf(key);
    if (!id) return;
    if (revealed[id]) {
      hide(id);
      return;
    }
    revealBusy = id;
    try {
      const response = await dashboardFetch(
        `/dashboard/api/inventory/keys/${encodeURIComponent(id)}/reveal`,
        {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' }
        }
      );
      if (!response.ok) throw new Error('Credential unavailable');
      const payload: unknown = await response.json();
      const value = text(object(payload).key_value, '');
      if (!value) throw new Error('Credential unavailable');
      revealed = { ...revealed, [id]: value };
      window.clearTimeout(revealTimers[id]);
      revealTimers[id] = window.setTimeout(() => hide(id), 30_000);
    } finally {
      revealBusy = '';
    }
  }

  function hide(id: string) {
    window.clearTimeout(revealTimers[id]);
    const next = { ...revealed };
    delete next[id];
    revealed = next;
  }

  async function copy(id: string) {
    const value = revealed[id];
    if (!value) return;
    await copyText(value);
    copied = id;
    window.setTimeout(() => {
      if (copied === id) copied = '';
    }, 1600);
  }

  onDestroy(() => Object.values(revealTimers).forEach((timer) => window.clearTimeout(timer)));
</script>

<PageHeader
  title="Credential inventory"
  description="Monitor upstream accounts, available capacity, and routing readiness in one place."
>
  <button
    class="button"
    on:click={() =>
      action('/dashboard/api/inventory/recheck-all', { success: 'Inventory recheck started' })}
  >
    <Icon name="refresh" />Recheck all
  </button>
  <button class="button primary" on:click={() => navigate('/dashboard/ui/inventory/add')}>
    <Icon name="plus" />Add credentials
  </button>
</PageHeader>

<nav class="inventory-tabs" aria-label="Credential inventory sections">
  {#each tabs as tab}
    <button class:active={tab.label === 'Overview'} on:click={() => navigate(tab.href)}>
      {tab.label}
    </button>
  {/each}
</nav>

<div class="stats-grid">
  <StatCard
    label="Stored keys"
    value={compact(summary.total)}
    detail={`${compact(summary.providers)} connected providers`}
  />
  <StatCard
    label="Ready to route"
    value={compact(summary.usable ?? summary.active)}
    detail={`${compact(summary.pending)} awaiting validation`}
    tone="teal"
  />
  <StatCard
    label="Available credits"
    value={money(creditTotal)}
    detail="Reported by credit-based providers"
    tone="violet"
  />
  <StatCard
    label="Needs attention"
    value={compact(number(summary.invalid) + number(summary.exhausted))}
    detail="Invalid or exhausted credentials"
    tone="amber"
  />
</div>

<section class="security-card" class:warning={plaintextCount > 0}>
  <div class="security-icon">
    <Icon name={plaintextCount > 0 ? 'warning' : 'vault'} size={20} />
  </div>
  <div class="security-copy">
    <span class="eyebrow">Credential protection</span>
    <strong>
      {plaintextCount > 0
        ? `${compact(plaintextCount)} credentials still need encryption`
        : 'Stored credentials are protected'}
    </strong>
    <p>
      {encryptedCount > 0
        ? `${compact(encryptedCount)} encrypted values across inventory and provider storage.`
        : 'Encryption status will appear after the first credential is stored.'}
    </p>
  </div>
  {#if plaintextCount > 0 && data.encryption_enabled}
    <button
      class="button"
      on:click={() =>
        action('/dashboard/api/inventory/encrypt-keys', {
          success: 'Credential encryption updated'
        })}
    >
      Encrypt now
    </button>
  {:else if plaintextCount > 0}
    <span class="status warning">Key required</span>
  {:else}
    <span class="status active">Protected</span>
  {/if}
</section>

{#if bestKeys.length}
  <section class="section-block">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Routing candidates</span>
        <h2>Best key per provider</h2>
      </div>
      <p>Highest reported capacity. Revealed values disappear after 30 seconds.</p>
    </div>
    <div class="best-grid">
      {#each bestKeys as key}
        {@const id = idOf(key)}
        <article class="best-card">
          <header>
            <div>
              <strong>{text(key.provider_display_name ?? key.provider_id)}</strong>
              <span>{text(key.key_label, 'Highest-capacity credential')}</span>
            </div>
            <div class="balance">
              <strong>{money(key.credits_remaining)}</strong>
              {#if key.credits_total}<span>of {money(key.credits_total)}</span>{/if}
            </div>
          </header>
          <div class="credential-line">
            <code class:revealed={!!revealed[id]}>
              {revealed[id] || text(key.key_masked, '••••••••')}
            </code>
            <button
              class="button compact-button"
              disabled={revealBusy === id}
              on:click={() => reveal(key)}
            >
              {revealed[id] ? 'Hide' : revealBusy === id ? 'Loading…' : 'Reveal'}
            </button>
            <button
              class="icon-button"
              aria-label="Copy revealed credential"
              title="Copy"
              disabled={!revealed[id]}
              on:click={() => copy(id)}
            >
              <Icon name={copied === id ? 'check' : 'download'} size={14} />
            </button>
          </div>
          <footer>
            <span class="status active">{text(key.status, 'active')}</span>
            <span>
              {key.rate_limit_rpm ? `${compact(key.rate_limit_rpm)} RPM` : 'Limit not reported'}
            </span>
          </footer>
        </article>
      {/each}
    </div>
  </section>
{/if}

<div class="panel-grid equal inventory-panels">
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Provider coverage</h2>
        <p>Credential health grouped by upstream</p>
      </div>
      <button class="button ghost" on:click={() => navigate('/dashboard/ui/inventory/keys')}>
        View all <Icon name="arrow" size={14} />
      </button>
    </div>
    {#if providers.length}
      <div class="provider-list">
        {#each providers as provider}
          {@const total = Math.max(1, number(provider.total_keys))}
          {@const usable = number(provider.usable_keys ?? provider.active_keys)}
          <button
            class="provider-row"
            on:click={() =>
              navigate(
                `/dashboard/ui/inventory/keys?provider_id=${encodeURIComponent(text(provider.provider_id ?? provider.id, ''))}`
              )}
          >
            <span class="provider-avatar">
              {text(provider.display_name ?? provider.provider_id, '?')
                .slice(0, 2)
                .toUpperCase()}
            </span>
            <span class="provider-copy">
              <strong>{text(provider.display_name ?? provider.provider_id)}</strong>
              <small>{compact(usable)} usable · {compact(provider.invalid_keys)} invalid</small>
              <span class="progress">
                <span style={`width:${Math.min(100, (usable / total) * 100)}%`}></span>
              </span>
            </span>
            <span class="provider-total">
              <strong>{compact(provider.total_keys)}</strong>
              <small>keys</small>
            </span>
            <Icon name="arrow" size={15} />
          </button>
        {/each}
      </div>
    {:else}
      <EmptyState
        icon="vault"
        title="No inventory yet"
        message="Add upstream credentials to unlock account-aware routing."
      >
        <button class="button primary" on:click={() => navigate('/dashboard/ui/inventory/add')}>
          Add credentials
        </button>
      </EmptyState>
    {/if}
  </section>

  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Credit pools</h2>
        <p>Latest balances reported by providers</p>
      </div>
    </div>
    {#if credits.length}
      <div class="credit-list">
        {#each credits as row}
          {@const remaining = number(row.total_remaining)}
          {@const cap = number(row.total_cap)}
          <div class="credit-row">
            <div>
              <strong>{text(row.display_name ?? row.provider_id)}</strong>
              <small>{compact(row.key_count)} keys · {text(row.billing_model, 'usage')}</small>
            </div>
            <div class="credit-value">
              <strong>{money(remaining)}</strong>
              {#if cap > 0}<small>
                  {Math.round((remaining / cap) * 100)}% remaining
                </small>{:else}<small>available</small>{/if}
            </div>
          </div>
        {/each}
      </div>
    {:else}
      <EmptyState
        icon="wallet"
        title="No credit data"
        message="Balances appear after providers complete their first validation."
      />
    {/if}
  </section>
</div>

<section class="panel activity-panel">
  <div class="panel-header">
    <div>
      <h2>Recent inventory activity</h2>
      <p>Status transitions and balance snapshots</p>
    </div>
  </div>
  {#if activity.length}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Credential</th>
            <th>Provider</th>
            <th>Transition</th>
            <th>Balance</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {#each activity as item}<tr>
              <td data-label="Credential"><span class="mono">{text(item.key_masked)}</span></td>
              <td data-label="Provider">{text(item.provider_display_name ?? item.provider_id)}</td>
              <td data-label="Transition">
                <span class="status {text(item.new_status, 'pending')}">
                  {text(item.previous_status, 'new')} → {text(item.new_status)}
                </span>
              </td>
              <td data-label="Balance">
                {item.credits_remaining == null ? '—' : money(item.credits_remaining)}
              </td>
              <td data-label="When" class="muted">{dateTime(item.changed_at)}</td>
            </tr>{/each}
        </tbody>
      </table>
    </div>
  {:else}
    <EmptyState
      icon="pulse"
      title="No inventory activity yet"
      message="Validation and status changes will be recorded here."
    />
  {/if}
</section>

<style>
  .inventory-tabs {
    display: flex;
    gap: 5px;
    width: max-content;
    max-width: 100%;
    padding: 4px;
    margin: -10px 0 22px;
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--surface);
  }
  .inventory-tabs button {
    padding: 8px 13px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--muted);
    font-size: 11px;
    font-weight: 680;
    cursor: pointer;
    white-space: nowrap;
  }
  .inventory-tabs button:hover {
    color: var(--text);
    background: var(--surface-soft);
  }
  .inventory-tabs button.active {
    color: var(--accent-strong);
    background: var(--accent-soft);
  }
  .security-card {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 17px 19px;
    margin-bottom: 25px;
    border: 1px solid color-mix(in srgb, var(--success) 22%, var(--line));
    border-radius: 16px;
    background: color-mix(in srgb, var(--success) 5%, var(--surface));
    box-shadow: 0 4px 18px rgba(44, 85, 92, 0.035);
  }
  .security-card.warning {
    border-color: color-mix(in srgb, var(--warning) 28%, var(--line));
    background: color-mix(in srgb, var(--warning) 6%, var(--surface));
  }
  .security-icon {
    display: grid;
    place-items: center;
    width: 39px;
    height: 39px;
    flex: 0 0 auto;
    border-radius: 12px;
    background: color-mix(in srgb, var(--success) 13%, transparent);
    color: var(--success);
  }
  .warning .security-icon {
    color: var(--warning);
    background: var(--warning-soft);
  }
  .security-copy {
    flex: 1;
    min-width: 0;
  }
  .security-copy strong {
    display: block;
    margin: 3px 0 2px;
    font-size: 13px;
  }
  .security-copy p {
    margin: 0;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.45;
  }
  .section-block {
    margin: 0 0 25px;
  }
  .section-heading {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 20px;
    margin: 0 1px 13px;
  }
  .section-heading h2 {
    margin: 5px 0 0;
    font-size: 17px;
    letter-spacing: -0.02em;
  }
  .section-heading p {
    margin: 0;
    color: var(--muted);
    font-size: 10px;
  }
  .best-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 13px;
  }
  .best-card {
    padding: 17px;
    border: 1px solid var(--line);
    border-radius: 16px;
    background: var(--surface);
    box-shadow: 0 4px 18px rgba(44, 85, 92, 0.035);
  }
  .best-card header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  .best-card header > div:first-child strong,
  .best-card header > div:first-child span,
  .balance strong,
  .balance span {
    display: block;
  }
  .best-card header > div:first-child strong {
    font-size: 13px;
  }
  .best-card header > div:first-child span {
    margin-top: 4px;
    color: var(--muted);
    font-size: 10px;
  }
  .balance {
    text-align: right;
  }
  .balance strong {
    color: var(--success);
    font-size: 18px;
    letter-spacing: -0.03em;
  }
  .balance span {
    color: var(--faint);
    font-size: 9px;
  }
  .credential-line {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 15px 0 12px;
    padding: 7px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: var(--surface-soft);
  }
  .credential-line code {
    min-width: 0;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--muted);
    font:
      10px ui-monospace,
      monospace;
    white-space: nowrap;
  }
  .credential-line code.revealed {
    color: var(--accent-strong);
  }
  .credential-line .icon-button {
    width: 29px;
    height: 29px;
    border-radius: 8px;
  }
  .compact-button {
    min-height: 29px;
    padding: 5px 9px;
    font-size: 10px;
  }
  .best-card footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--muted);
    font-size: 10px;
  }
  .inventory-panels {
    margin-bottom: 18px;
  }
  .provider-list,
  .credit-list {
    display: grid;
  }
  .provider-row {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 14px 18px;
    border: 0;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 72%, transparent);
    background: transparent;
    color: var(--text);
    text-align: left;
    cursor: pointer;
  }
  .provider-row:last-child,
  .credit-row:last-child {
    border-bottom: 0;
  }
  .provider-row:hover {
    background: color-mix(in srgb, var(--accent-soft) 32%, transparent);
  }
  .provider-avatar {
    display: grid;
    place-items: center;
    width: 35px;
    height: 35px;
    flex: 0 0 auto;
    border-radius: 11px;
    color: var(--accent-strong);
    background: var(--accent-soft);
    font-size: 10px;
    font-weight: 800;
  }
  .provider-copy {
    display: block;
    flex: 1;
    min-width: 0;
  }
  .provider-copy strong,
  .provider-copy small,
  .provider-total strong,
  .provider-total small {
    display: block;
  }
  .provider-copy strong,
  .credit-row strong {
    font-size: 12px;
  }
  .provider-copy small,
  .provider-total small,
  .credit-row small {
    margin: 3px 0 7px;
    color: var(--muted);
    font-size: 9px;
  }
  .provider-copy .progress {
    height: 4px;
  }
  .provider-total {
    text-align: right;
  }
  .provider-total strong {
    font-size: 14px;
  }
  .credit-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 17px 20px;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 72%, transparent);
  }
  .credit-value {
    text-align: right;
  }
  .credit-value strong {
    color: var(--success);
  }
  .credit-value small {
    display: block;
    margin: 3px 0 0;
  }
  .activity-panel {
    margin-top: 18px;
  }
  @media (max-width: 1100px) {
    .best-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 700px) {
    .inventory-tabs {
      width: 100%;
      overflow-x: auto;
    }
    .security-card {
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .security-copy {
      min-width: calc(100% - 58px);
    }
    .security-card > .button,
    .security-card > .status {
      margin-left: 53px;
    }
    .best-grid {
      grid-template-columns: 1fr;
    }
    .section-heading {
      display: block;
    }
    .section-heading p {
      margin-top: 5px;
    }
    .credit-row {
      padding: 15px;
    }
    .activity-panel {
      margin-top: 14px;
    }
  }
</style>
