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
  $: budgets = firstList(data, 'budgets', 'items');
  $: keys = firstList(data, 'keys', 'api_keys');
  const cols = [
    {
      key: 'key_name',
      label: 'Scope',
      format: (v: unknown, r: JsonObject) => text(v ?? r.name, 'Global')
    },
    { key: 'daily_limit', label: 'Daily limit', format: money },
    {
      key: 'spent',
      label: 'Spent today',
      format: (v: unknown, r: JsonObject) => money(v ?? object(r.status).today_spend)
    },
    { key: 'warn_pct', label: 'Warn at', format: percent }
  ];
  async function submit(e: SubmitEvent) {
    await action('/dashboard/api/budgets', {
      body: new FormData(e.currentTarget as HTMLFormElement),
      success: 'Budget saved'
    });
    open = false;
  }
</script>

<PageHeader
  title="Budgets"
  description={`Daily spend guardrails use the configured ${text(data.reporting_timezone, 'reporting timezone')}.`}
>
  <button class="button primary" on:click={() => (open = true)}>
    <Icon name="plus" />Set budget
  </button>
</PageHeader>
<section class="panel">
  <DataTable rows={budgets} columns={cols} emptyTitle="No budgets configured">
    <svelte:fragment slot="actions" let:row>
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
  title="Set daily budget"
  description="The most restrictive matching budget wins."
  on:close={() => (open = false)}
>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field full">
        <span>Scope</span>
        <select name="key_select" required>
          <option value="global">Global gateway</option>
          {#each keys as key}<option value={idOf(key)}>
              {text(key.name ?? key.key_name)}
            </option>{/each}
        </select>
      </label>
      <label class="field">
        <span>Daily limit (USD)</span>
        <input name="daily_limit" type="number" min="0.01" step="0.01" required />
      </label>
      <label class="field">
        <span>Warn at (%)</span>
        <input name="warn_pct" type="number" min="1" max="100" value="80" required />
      </label>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">Save budget</button>
    </div>
  </form>
</Modal>
