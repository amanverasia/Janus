<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { dashboardFetch, responseError } from '$lib/api';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import MiniChart from '$lib/components/MiniChart.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import StatCard from '$lib/components/StatCard.svelte';
  import { compact, dateTime, firstList, money, number, object, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';

  export let data: JsonObject;

  let connected = false;
  let inflight = 0;
  let recent: JsonObject[] = [];
  let stream: AbortController | undefined;
  let reconnect: ReturnType<typeof setTimeout> | undefined;

  $: stats = object(data.stats ?? data.summary);
  $: historical = firstList(stats, 'daily', 'series');
  $: values = historical.map((point) => number(point.requests ?? point.value));

  function applyLivePayload(payload: JsonObject) {
    if (typeof payload.inflight === 'number') inflight = payload.inflight;
    if (payload.type === 'inflight') inflight = number(payload.count, inflight);
    if (Array.isArray(payload.recent)) {
      recent = payload.recent.map(object).reverse();
    } else if (payload.type === 'request') {
      recent = [payload, ...recent].slice(0, 20);
    }
  }

  function eventTime(event: JsonObject): string {
    const raw = event.timestamp ?? event.created_at ?? event.ts;
    if (typeof raw === 'number') {
      const milliseconds = raw < 1_000_000_000_000 ? raw * 1000 : raw;
      return dateTime(new Date(milliseconds).toISOString());
    }
    return dateTime(raw);
  }

  function parseEventBlock(block: string) {
    const payload = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    if (!payload) return;
    try {
      applyLivePayload(object(JSON.parse(payload)));
    } catch {
      return;
    }
  }

  async function connect() {
    if (reconnect) clearTimeout(reconnect);
    stream?.abort();
    const controller = new AbortController();
    stream = controller;
    try {
      const response = await dashboardFetch('/dashboard/api/usage/live', {
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal
      });
      if (!response.ok) throw new Error(await responseError(response));
      if (!response.body) throw new Error('Live usage streaming is unavailable.');

      connected = true;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = '';
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const blocks = pending.split(/\r?\n\r?\n/);
        pending = blocks.pop() ?? '';
        blocks.forEach(parseEventBlock);
      }
      if (!controller.signal.aborted) throw new Error('Live usage stream closed.');
    } catch {
      if (controller.signal.aborted) return;
      connected = false;
      reconnect = setTimeout(() => void connect(), 5000);
    }
  }

  onMount(() => {
    dashboardFetch('/dashboard/api/usage/snapshot', {
      headers: { Accept: 'application/json' }
    })
      .then((response) => (response.ok ? response.json() : {}))
      .then((payload: JsonObject) => applyLivePayload(payload))
      .catch(() => undefined);
    void connect();
  });

  onDestroy(() => {
    stream?.abort();
    if (reconnect) clearTimeout(reconnect);
  });
</script>

<PageHeader
  title="Usage pulse"
  description="Live activity and historical throughput, together in one calm operational view."
>
  <span class="status {connected ? 'active' : 'pending'}">
    {connected ? 'Live' : 'Reconnecting'}
  </span>
</PageHeader>
<div class="stats-grid">
  <StatCard
    label="In flight"
    value={String(inflight)}
    detail="Requests currently routing"
  /><StatCard
    label="Requests"
    value={compact(stats.total_requests)}
    detail="Selected period"
    tone="teal"
  /><StatCard
    label="Input tokens"
    value={compact(stats.total_input_tokens ?? stats.input_tokens)}
    tone="violet"
  /><StatCard label="Total cost" value={money(stats.total_cost)} tone="amber" />
</div>
<div class="panel-grid">
  <section class="panel chart-card">
    <div class="panel-header">
      <div>
        <h2>Traffic trend</h2>
        <p>Request volume by reporting interval</p>
      </div>
    </div>
    <div class="panel-body"><MiniChart {values} label="Usage over time" /></div>
  </section>
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>Live requests</h2>
        <p>Newest gateway events</p>
      </div>
    </div>
    {#if recent.length}<div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Status</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {#each recent.slice(0, 8) as event}<tr>
                <td data-label="Model">{text(event.model ?? event.request_model)}</td>
                <td data-label="Status">
                  <span
                    class="status {number(event.status ?? event.status_code) >= 400
                      ? 'error'
                      : 'active'}"
                  >
                    {text(event.status ?? event.status_code, 'ok')}
                  </span>
                </td>
                <td data-label="When">{eventTime(event)}</td>
              </tr>{/each}
          </tbody>
        </table>
      </div>{:else}<EmptyState
        icon="pulse"
        title="Waiting for traffic"
        message="Live requests will appear here as they reach Janus."
      />{/if}
  </section>
</div>
