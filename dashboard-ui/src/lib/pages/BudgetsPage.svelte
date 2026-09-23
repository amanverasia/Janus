<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import DataTable from '$lib/components/DataTable.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { firstList, idOf, money, object, percent, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';
  export let data: JsonObject;
  export let action: (u: string, o?: MutationOptions) => Promise<unknown>;
  let open = false;
  let scope = 'global';
  let dailyLimit: number | undefined;
  let absoluteLimit: number | undefined;
  let warnPct = 80;
  $: budgets = firstList(data, 'budgets', 'items');
  $: keys = firstList(data, 'keys', 'api_keys');
  const cols = [
    {
      key: 'key_name',
      label: 'Scope',
      format: (v: unknown, r: JsonObject) => text(v ?? r.name, 'Global')
    },
    {
      key: 'daily_limit',
      label: 'Daily limit',
      format: (v: unknown) => (v == null ? 'No limit' : money(v))
    },
    {
      key: 'spent',
      label: 'Spent today',
      format: (v: unknown, r: JsonObject) => money(v ?? object(r.status).today_spend)
    },
    {
      key: 'absolute_limit',
      label: 'Absolute limit',
      format: (v: unknown) => (v == null ? 'No limit' : money(v))
    },
    {
      key: 'total_spend',
      label: 'Spent total',
      format: (_: unknown, r: JsonObject) =>
        object(r.status).total_spend == null ? '—' : money(object(r.status).total_spend)
    },
    { key: 'warn_pct', label: 'Warn at', format: percent },
    { key: 'status', label: 'Status', format: (v: unknown) => text(object(v).status) }
  ];

  function loadScope(selected: string) {
    scope = selected;
    const budget = budgets.find((row) => text(row.key_id, 'global') === selected);
    dailyLimit = budget?.daily_limit == null ? undefined : Number(budget.daily_limit);
    absoluteLimit = budget?.absolute_limit == null ? undefined : Number(budget.absolute_limit);
    warnPct = budget?.warn_pct == null ? 80 : Number(budget.warn_pct);
  }

  function edit(row?: JsonObject) {
    loadScope(text(row?.key_id, 'global'));
    open = true;
  }

  async function submit(e: SubmitEvent) {
    try {
      await action('/dashboard/api/budgets', {
        body: new FormData(e.currentTarget as HTMLFormElement),
        success: 'Budget saved'
      });
    } catch {
      return;
    }
    open = false;
  }
</script>

<PageHeader
  title="Budgets"
  description={`Daily limits reset at midnight (${text(data.reporting_timezone, 'reporting timezone')}). Per-key absolute limits never reset.`}
>
  <button class="button primary" on:click={() => edit()}>
    <Icon name="plus" />Set budget
  </button>
</PageHeader>
<section class="panel">
  <DataTable rows={budgets} columns={cols} emptyTitle="No budgets configured">
    <svelte:fragment slot="actions" let:row>
      <button
        class="icon-button"
        title="Edit budget"
        aria-label={`Edit budget for ${text(row.key_name, 'Global')}`}
        on:click={() => edit(row)}
      >
        <Icon name="edit" size={15} />
      </button>
      <button
        class="icon-button"
        title="Delete"
        on:click={() =>
          confirm('Delete this budget?') &&
          action(`/dashboard/api/budgets/${idOf(row)}`, {
            method: 'DELETE',
            success: 'Budget deleted'
          })}
      >
        <Icon name="trash" size={15} />
      </button>
    </svelte:fragment>
  </DataTable>
</section>
<Modal
  {open}
  title="Set budget"
  description="Requests are blocked when any matching daily or absolute limit is reached."
  on:close={() => (open = false)}
>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field full">
        <span>Scope</span>
        <select
          name="key_select"
          value={scope}
          on:change={(event) => loadScope(event.currentTarget.value)}
          required
        >
          <option value="global">Global gateway (daily only)</option>
          {#each keys as key}<option value={idOf(key)}>
              {text(key.name ?? key.key_name)}
            </option>{/each}
        </select>
      </label>
      <label class="field">
        <span>Daily limit (USD)</span>
        <input
          name="daily_limit"
          type="number"
          min="0.01"
          step="0.01"
          bind:value={dailyLimit}
          placeholder="No limit"
          required={scope === 'global' || absoluteLimit == null}
        />
      </label>
      <label class="field">
        <span>Absolute limit (USD)</span>
        <input
          name="absolute_limit"
          type="number"
          min="0.01"
          step="0.01"
          bind:value={absoluteLimit}
          placeholder={scope === 'global' ? 'Select an API key first' : 'No limit'}
          disabled={scope === 'global'}
          required={scope !== 'global' && dailyLimit == null}
          aria-describedby="absolute-budget-help"
        />
      </label>
      <p id="absolute-budget-help" class="field full muted">
        {#if scope === 'global'}
          Absolute budgets require a specific API key.
          {#if keys.length}
            To set an absolute limit, select an API key in Scope above.
          {:else}
            <a href="/dashboard/ui/keys">Create an API key</a>
          {/if}
        {:else}
          The absolute limit counts all recorded spending for this key, including past usage, and
          never resets. Use either limit or both. Leave a limit blank to remove it.
        {/if}
      </p>
      <label class="field">
        <span>Warn at (%)</span>
        <input name="warn_pct" type="number" min="1" max="100" bind:value={warnPct} required />
      </label>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">Save budget</button>
    </div>
  </form>
</Modal>
