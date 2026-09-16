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
  let showBodies = false;
  // Rapid clicks resolved out of order and showed the wrong row's detail.
  let detailRequest: AbortController | undefined;

  $: rows = firstList(data, 'logs', 'request_logs', 'items');
  $: offset = Number(data.offset ?? 0);
  $: limit = Number(data.limit ?? 100);
  $: total = Number(data.total ?? rows.length);

  function providerLabel(value: unknown, row: JsonObject): string {
    const raw = text(value ?? row.provider, '');
    if (!raw) return '—';
    const short = raw.includes('::') ? raw.split('::')[0] : raw;
    return short.length > 28 ? `${short.slice(0, 25)}…` : short;
  }

  const columns = [
    { key: 'timestamp', label: 'Time', format: dateTime },
    { key: 'model', label: 'Model' },
    {
      key: 'provider_id',
      label: 'Provider',
      format: (value: unknown, row: JsonObject) => providerLabel(value, row)
    },
    { key: 'status', label: 'Status', format: (value: unknown) => text(value) },
    { key: 'duration_ms', label: 'Latency', format: (value: unknown) => `${compact(value)} ms` }
  ];

  async function inspect(row: JsonObject) {
    detailRequest?.abort();
    const controller = new AbortController();
    detailRequest = controller;
    detailError = '';
    detail = undefined;
    showBodies = false;
    detailOpen = true;
    try {
      const response = await dashboardFetch(
        `/dashboard/api/request-logs/${encodeURIComponent(idOf(row))}`,
        {
          headers: { Accept: 'application/json' },
          signal: controller.signal
        }
      );
      if (!response.ok) throw new Error(await responseError(response));
      const payload: unknown = await response.json();
      if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('The request detail response was not valid.');
      }
      if (detailRequest !== controller) return;
      detail = payload as JsonObject;
    } catch (error) {
      if (controller.signal.aborted || detailRequest !== controller) return;
      detailError = error instanceof Error ? error.message : 'Request details could not be loaded.';
    }
  }

  function detailField(label: string, value: unknown): { label: string; value: string } {
    return { label, value: text(value, '—') };
  }

  $: detailFields = detail
    ? [
        detailField('Time', detail.timestamp ?? detail.created_at),
        detailField('Model', detail.model),
        detailField('Provider', providerLabel(detail.provider_id ?? detail.provider, detail)),
        detailField('Status', detail.status),
        detailField(
          'Latency',
          detail.duration_ms != null ? `${compact(detail.duration_ms)} ms` : '—'
        ),
        detailField('Stream', detail.stream ?? detail.is_stream),
        detailField('Error', detail.error ?? detail.error_message)
      ]
    : [];

  $: hasBodies =
    !!detail &&
    (detail.request_body != null ||
      detail.response_body != null ||
      detail.body != null ||
      detail.request != null);

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
  {#if rows.length}<div class="panel-body" style="display:flex;justify-content:flex-end;gap:8px">
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
    </div>{/if}
</section>

<Modal open={detailOpen} title="Request detail" wide on:close={() => (detailOpen = false)}>
  {#if detailError}<div class="alert-strip error" role="alert">
      <Icon name="warning" size={17} />
      <div><span>{detailError}</span></div>
    </div>{:else if detail}
    <div class="detail-grid">
      {#each detailFields as field}
        <div>
          <span>{field.label}</span>
          <strong title={field.value}>{field.value}</strong>
        </div>
      {/each}
    </div>
    {#if hasBodies}
      <div class="detail-body-controls">
        <button class="button" on:click={() => (showBodies = !showBodies)}>
          {showBodies ? 'Hide bodies' : 'Show bodies'}
        </button>
      </div>
      {#if showBodies}
        <pre class="code-block">{JSON.stringify(
            {
              request_body: detail.request_body ?? detail.request ?? detail.body,
              response_body: detail.response_body ?? detail.response
            },
            null,
            2
          )}</pre>
      {/if}
    {/if}
  {:else}<div class="loading-state">Loading request detail…</div>{/if}
</Modal>

<style>
  .detail-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
  }
  .detail-grid span {
    display: block;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .detail-grid strong {
    display: block;
    word-break: break-word;
    font-size: 13px;
  }
  .detail-body-controls {
    margin: 16px 0 10px;
  }
</style>
