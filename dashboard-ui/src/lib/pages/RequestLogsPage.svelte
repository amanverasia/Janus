<script lang="ts">
  import { dashboardFetch, responseError } from '$lib/api';
  import DataTable from '$lib/components/DataTable.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { compact, dateTime, firstList, idOf, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigateQuery: (params: Record<string, string>) => void;

  let detail: JsonObject | undefined;
  let detailOpen = false;
  let detailError = '';

  $: rows = firstList(data, 'logs', 'request_logs', 'items');
  $: offset = Number(data.offset ?? 0);
  $: limit = Number(data.limit ?? 100);
  $: total = Number(data.total ?? rows.length);

  const columns = [
    { key: 'created_at', label: 'Time', format: dateTime },
    { key: 'model', label: 'Model' },
    {
      key: 'provider_id',
      label: 'Provider',
      format: (value: unknown, row: JsonObject) => text(value ?? row.provider)
    },
    { key: 'status_code', label: 'Status', format: (value: unknown) => text(value) },
    { key: 'latency_ms', label: 'Latency', format: (value: unknown) => `${compact(value)} ms` }
  ];

  async function inspect(row: JsonObject) {
    detailError = '';
    detail = undefined;
    detailOpen = true;
    try {
      const response = await dashboardFetch(
        `/dashboard/api/request-logs/${encodeURIComponent(idOf(row))}`,
        {
          headers: { Accept: 'application/json' }
        }
      );
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('The request detail response was not valid.');
      }
      detail = payload as JsonObject;
    } catch (error) {
      detailError = error instanceof Error ? error.message : 'Request details could not be loaded.';
    }
  }

  async function clearLogs() {
    const confirmed = window.confirm(
      'Permanently clear every retained request log? This cannot be undone.'
    );
    if (!confirmed) return;
    await action('/dashboard/api/request-logs', {
      method: 'DELETE',
      success: 'Request logs cleared'
    });
  }
</script>

<PageHeader
  title="Request logs"
  description="Inspect recent gateway requests without exposing request bodies in the table."
>
  <a class="button" href="/dashboard/api/request-logs/export" download>
    <Icon name="download" />Export JSON
  </a>
  <button class="button danger" on:click={clearLogs}><Icon name="trash" />Clear logs</button>
</PageHeader>

<section class="panel">
  <div class="panel-header">
    <div>
      <h2>Recent requests</h2>
      <p>{compact(total)} retained events</p>
    </div>
    <span class="status {data.logging_enabled === false ? 'disabled' : 'active'}">
      {data.logging_enabled === false ? 'Disabled' : 'Recording'}
    </span>
  </div>
  <DataTable {rows} {columns} emptyTitle="No request logs">
    <svelte:fragment slot="actions" let:row>
      <button class="icon-button" title="Inspect" on:click={() => inspect(row)}>
        <Icon name="eye" size={15} />
      </button>
    </svelte:fragment>
  </DataTable>
  <div class="panel-body" style="display:flex;justify-content:flex-end;gap:8px">
    <button
      class="button"
      disabled={offset <= 0}
      on:click={() =>
        navigateQuery({ offset: String(Math.max(0, offset - limit)), limit: String(limit) })}
    >
      Previous
    </button>
    <button
      class="button"
      disabled={offset + limit >= total}
      on:click={() => navigateQuery({ offset: String(offset + limit), limit: String(limit) })}
    >
      Next
    </button>
  </div>
</section>

<Modal open={detailOpen} title="Request detail" wide on:close={() => (detailOpen = false)}>
  {#if detailError}<div class="file-error" role="alert">{detailError}</div>{:else if detail}<pre
      class="code-block">{JSON.stringify(detail, null, 2)}</pre>{:else}<div class="loading-state">
      Loading request detail…
    </div>{/if}
</Modal>
