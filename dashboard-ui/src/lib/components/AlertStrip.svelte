<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { AlertItem } from '$lib/types';
  import Icon from './Icon.svelte';
  export let alerts: AlertItem[] = [];
  const dispatch = createEventDispatcher<{ navigate: string }>();

  const LEVELS = new Set(['info', 'warning', 'error', 'danger', 'critical']);

  // Alert levels arrive from the API. Map them onto a known class instead of
  // interpolating server text straight into the class attribute.
  function tone(alert: AlertItem): string {
    const level = String(alert.level ?? alert.severity ?? 'info').toLowerCase();
    return LEVELS.has(level) ? level : 'info';
  }

  function follow(event: MouseEvent, href: string) {
    // Let the browser handle modified clicks (new tab, new window).
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    dispatch('navigate', href);
  }
</script>

{#if alerts.length}<div class="alert-stack">
    {#each alerts as alert}<div class="alert-strip {tone(alert)}">
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
      </div>{/each}
  </div>{/if}
