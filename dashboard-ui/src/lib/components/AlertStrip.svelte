<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import type { AlertItem } from '$lib/types';
  import Icon from './Icon.svelte';
  export let alerts: AlertItem[] = [];
  const dispatch = createEventDispatcher<{ navigate: string }>();

  const LEVELS = new Set(['info', 'warning', 'error', 'danger', 'critical']);
  const DISMISS_KEY = 'janus-dismissed-alerts';
  const SEVERITY_RANK: Record<string, number> = {
    critical: 5,
    danger: 4,
    error: 3,
    warning: 2,
    info: 1
  };

  let expanded = false;
  let dismissed = new Set<string>();

  onMount(() => {
    try {
      const raw = sessionStorage.getItem(DISMISS_KEY);
      if (raw) {
        const parsed: unknown = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          dismissed = new Set(parsed.map(String));
        }
      }
    } catch {
      dismissed = new Set();
    }
  });

  $: visible = alerts.filter((alert) => {
    const id = alertId(alert);
    return !id || !dismissed.has(id);
  });
  $: collapsed = !expanded && visible.length > 1;
  $: summaryTone = highestTone(visible);

  function alertId(alert: AlertItem): string {
    return String(alert.id ?? `${alert.title ?? ''}:${alert.message ?? alert.detail ?? ''}`);
  }

  function tone(alert: AlertItem): string {
    const level = String(alert.level ?? alert.severity ?? 'info').toLowerCase();
    return LEVELS.has(level) ? level : 'info';
  }

  function highestTone(items: AlertItem[]): string {
    let best = 'info';
    let rank = 0;
    for (const alert of items) {
      const current = tone(alert);
      const value = SEVERITY_RANK[current] ?? 0;
      if (value > rank) {
        rank = value;
        best = current;
      }
    }
    return best;
  }

  function follow(event: MouseEvent, href: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    dispatch('navigate', href);
  }

  function dismiss(alert: AlertItem) {
    const id = alertId(alert);
    if (!id) return;
    dismissed = new Set([...dismissed, id]);
    try {
      sessionStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissed]));
    } catch {
      /* ignore quota / private mode */
    }
  }
</script>

{#if visible.length}<div class="alert-stack">
    {#if collapsed}
      <button
        type="button"
        class="alert-strip alert-summary {summaryTone}"
        on:click={() => (expanded = true)}
      >
        <Icon name="warning" size={17} />
        <div>
          <strong>{visible.length} alerts</strong>
          <span>Show issues that need attention across the dashboard.</span>
        </div>
        <span class="alert-link">Show</span>
      </button>
    {:else}
      {#if visible.length > 1}
        <div class="alert-toolbar">
          <button type="button" class="button ghost" on:click={() => (expanded = false)}>
            Hide alerts
          </button>
        </div>
      {/if}
      {#each visible as alert}<div class="alert-strip {tone(alert)}">
          <Icon name="warning" size={17} />
          <div>
            {#if alert.title}<strong>{alert.title}</strong>{/if}
            <span>{alert.message ?? alert.detail ?? 'Attention required'}</span>
          </div>
          {#if alert.href}<a
              class="alert-link"
              href={alert.href}
              on:click={(event) => follow(event, String(alert.href))}
            >
              View
            </a>{/if}
          <button
            type="button"
            class="icon-button alert-dismiss"
            title="Dismiss for this session"
            aria-label="Dismiss alert"
            on:click={() => dismiss(alert)}
          >
            <Icon name="x" size={14} />
          </button>
        </div>{/each}
    {/if}
  </div>{/if}

<style>
  .alert-toolbar {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 6px;
  }
  .alert-summary {
    width: 100%;
    text-align: left;
    cursor: pointer;
  }
  .alert-dismiss {
    margin-left: 4px;
  }
</style>
