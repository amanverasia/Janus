<script lang="ts">
  import { dashboardFetch, responseError } from '#lib/api.ts';
  import DataTable from '#lib/components/DataTable.svelte';
  import Icon from '#lib/components/Icon.svelte';
  import Modal from '#lib/components/Modal.svelte';
  import PageHeader from '#lib/components/PageHeader.svelte';
  import { compact, dateTime, firstList, idOf, list, number, object, text } from '#lib/data.ts';
  import type { JsonObject, MutationOptions } from '#lib/types.ts';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigateQuery: (params: Record<string, string>) => void;

  let detail: JsonObject | undefined;
  let detailOpen = false;
  let detailError = '';
  let showBodies = false;
  let loadingBodies = false;
  let detailLogId = '';
  let clearing = false;
  // Rapid clicks resolved out of order and showed the wrong row's detail.
  let detailRequest: AbortController | undefined;

  $: rows = firstList(data, 'logs', 'request_logs', 'items');
  $: offset = Number(data.offset ?? 0);
  $: limit = Number(data.limit ?? 100);
  $: total = Number(data.total ?? rows.length);
  $: attempts = detail ? list(detail.attempts) : [];
  $: attemptPagination = object(detail?.attempt_pagination);
  $: attemptOffset = Math.max(0, number(attemptPagination.offset, 0));
  $: attemptLimit = Math.max(1, number(attemptPagination.limit, 25));
  $: attemptTotal = Math.max(0, number(attemptPagination.total, attempts.length));
  $: correlationAvailable = object(detail?.correlation).available === true;
  $: finalOutcome = text(detail?.final_outcome, 'unknown').replaceAll('_', ' ');

  function providerLabel(value: unknown, row: JsonObject): string {
    const raw = text(value ?? row.provider, '');
    if (!raw) return '—';
    const short = raw.includes('::') ? raw.split('::')[0] : raw;
    return short.length > 28 ? `${short.slice(0, 25)}…` : short;
  }

  function keyLabel(value: unknown, row: JsonObject): string {
    return text(value ?? row.client_key_label, '—');
  }

  const columns = [
    { key: 'timestamp', label: 'Time', format: dateTime },
    { key: 'model', label: 'Model' },
    {
      key: 'resolved_model',
      label: 'Resolved',
      format: (value: unknown) => text(value, '—')
    },
    {
      key: 'provider_id',
      label: 'Provider',
      format: (value: unknown, row: JsonObject) => providerLabel(value, row)
    },
    {
      key: 'client_key_name',
      label: 'API key',
      format: (value: unknown, row: JsonObject) => keyLabel(value, row)
    },
    { key: 'status', label: 'Status', format: (value: unknown) => text(value) },
    { key: 'duration_ms', label: 'Latency', format: (value: unknown) => `${compact(value)} ms` },
    {
      key: 'error',
      label: 'Failure reason',
      format: (value: unknown) => {
        const reason = text(value, '—');
        return reason.length > 120 ? `${reason.slice(0, 117)}…` : reason;
      }
    }
  ];

  async function loadDetail(
    offset: number,
    includeBodies = false,
    preserve = false
  ): Promise<boolean> {
    detailRequest?.abort();
    const controller = new AbortController();
    detailRequest = controller;
    detailError = '';
    if (!preserve) detail = undefined;
    const query = new URLSearchParams({
      attempt_limit: String(attemptLimit),
      attempt_offset: String(offset)
    });
    if (includeBodies) query.set('include_bodies', 'true');
    try {
      const response = await dashboardFetch(
        `/dashboard/api/request-logs/${encodeURIComponent(detailLogId)}?${query}`,
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
      if (detailRequest !== controller) return false;
      detail = payload as JsonObject;
      return true;
    } catch (error) {
      if (controller.signal.aborted || detailRequest !== controller) return false;
      detailError = error instanceof Error ? error.message : 'Request details could not be loaded.';
      return false;
    }
  }

  async function inspect(row: JsonObject) {
    detailLogId = idOf(row);
    showBodies = false;
    detailOpen = true;
    await loadDetail(0);
  }

  async function changeAttemptPage(offset: number) {
    showBodies = false;
    await loadDetail(offset, false, true);
  }

  async function toggleBodies() {
    if (showBodies) {
      showBodies = false;
      return;
    }
    if (detail?.request_body != null || detail?.response_body != null) {
      showBodies = true;
      return;
    }
    if (loadingBodies) return;
    loadingBodies = true;
    const loaded = await loadDetail(attemptOffset, true, true);
    showBodies = loaded;
    loadingBodies = false;
  }

  function detailField(label: string, value: unknown): { label: string; value: string } {
    return { label, value: text(value, '—') };
  }

  $: detailFields = detail
    ? [
        detailField('Time', detail.timestamp ?? detail.created_at),
        detailField('Model', detail.model),
        detailField('Resolved model', detail.resolved_model),
        detailField('Provider', providerLabel(detail.provider_id ?? detail.provider, detail)),
        detailField('API key', detail.client_key_name ?? detail.client_key_label),
        detailField('Status', detail.status),
        detailField('Final outcome', finalOutcome),
        detailField('Request ID', detail.request_id),
        detailField(
          'Latency',
          detail.duration_ms != null ? `${compact(detail.duration_ms)} ms` : '—'
        ),
        detailField('Stream', detail.streamed ?? detail.stream ?? detail.is_stream),
        detailField('Error', detail.error ?? detail.error_message)
      ]
    : [];

  $: hasBodies =
    !!detail &&
    (detail.has_bodies === true ||
      detail.request_body != null ||
      detail.response_body != null ||
      detail.body != null ||
      detail.request != null);

  async function clearLogs() {
    const confirmed = window.confirm(
      'Permanently clear every retained request log? This cannot be undone.'
    );
    if (!confirmed || clearing) return;
    clearing = true;
    try {
      await action('/dashboard/api/request-logs', {
        method: 'DELETE',
        success: 'Request logs cleared'
      });
    } catch {
    } finally {
      clearing = false;
    }
  }
</script>

<PageHeader
  title="Request logs"
  description="Inspect recent gateway requests without exposing request bodies in the table."
>
  <a class="button" href="/dashboard/api/request-logs/export" download>
    <Icon name="download" />Export JSON
  </a>
  <button class="button danger" disabled={clearing} on:click={clearLogs}>
    <Icon name="trash" />{clearing ? 'Clearing…' : 'Clear logs'}
  </button>
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
    <section class="timeline" aria-label="Routing timeline">
      <div class="timeline-header">
        <h3>Routing timeline</h3>
        <span class="status {finalOutcome === 'success' ? 'active' : 'warning'}">
          {finalOutcome}
        </span>
      </div>
      {#if !correlationAvailable}
        <p class="muted">Legacy request log. Attempt correlation is unavailable for this row.</p>
      {:else if attempts.length}
        <ol class="attempt-list">
          {#each attempts as attempt}
            <li>
              <div class="attempt-heading">
                <strong>Attempt {text(attempt.attempt_order)}</strong>
                <span>
                  {text(attempt.outcome)}{attempt.status != null
                    ? ` · ${text(attempt.status)}`
                    : ''}
                </span>
              </div>
              <div class="attempt-meta">
                <span>{text(attempt.model)}</span>
                <span>{text(attempt.account, 'Unknown account')}</span>
                <span>{compact(attempt.duration_ms)} ms</span>
              </div>
              <div class="attempt-diagnostics">
                {#if attempt.failure_class}<span>
                    {text(attempt.failure_class).replaceAll('_', ' ')}
                  </span>{/if}
                {#if object(attempt.cooldown).decision && object(attempt.cooldown).decision !== 'none'}
                  <span>
                    Cooldown {text(object(attempt.cooldown).decision)}{object(attempt.cooldown)
                      .seconds != null
                      ? ` · ${compact(object(attempt.cooldown).seconds)}s`
                      : ''}
                  </span>
                {/if}
              </div>
              {#if object(attempt.recovery).href}
                <a class="recovery-link" href={text(object(attempt.recovery).href, '#')}>
                  {text(object(attempt.recovery).label, 'Review account')}
                </a>
              {/if}
            </li>
          {/each}
        </ol>
        {#if attemptTotal > attemptLimit}
          <div class="attempt-pagination">
            <button
              class="button"
              aria-label="Previous attempts"
              disabled={attemptOffset <= 0}
              on:click={() => changeAttemptPage(Math.max(0, attemptOffset - attemptLimit))}
            >
              Previous
            </button>
            <span>
              {attemptOffset + 1}–{Math.min(attemptOffset + attemptLimit, attemptTotal)} of {attemptTotal}
            </span>
            <button
              class="button"
              aria-label="Next attempts"
              disabled={attemptOffset + attemptLimit >= attemptTotal}
              on:click={() => changeAttemptPage(attemptOffset + attemptLimit)}
            >
              Next
            </button>
          </div>
        {/if}
      {:else}
        <p class="muted">No upstream attempts were recorded for this request.</p>
      {/if}
    </section>
    {#if hasBodies}
      <div class="detail-body-controls">
        <button class="button" disabled={loadingBodies} on:click={toggleBodies}>
          {loadingBodies ? 'Loading bodies…' : showBodies ? 'Hide bodies' : 'Show bodies'}
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
  .timeline {
    margin-top: 20px;
    border-top: 1px solid var(--border);
    padding-top: 16px;
  }
  .timeline-header,
  .attempt-heading,
  .attempt-meta,
  .attempt-diagnostics,
  .attempt-pagination {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .timeline-header {
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .timeline-header h3 {
    margin: 0;
    font-size: 15px;
  }
  .attempt-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  .attempt-list li {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
  }
  .attempt-heading {
    justify-content: space-between;
    font-size: 13px;
  }
  .attempt-meta,
  .attempt-diagnostics {
    color: var(--muted);
    font-size: 12px;
    margin-top: 7px;
  }
  .recovery-link {
    display: inline-block;
    margin-top: 9px;
    font-size: 12px;
  }
  .attempt-pagination {
    justify-content: flex-end;
    margin-top: 12px;
    font-size: 12px;
    color: var(--muted);
  }
  .detail-body-controls {
    margin: 16px 0 10px;
  }
</style>
