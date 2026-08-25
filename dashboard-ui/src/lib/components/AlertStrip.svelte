<script lang="ts">
  import type { AlertItem } from '$lib/types';
  import Icon from './Icon.svelte';
  export let alerts: AlertItem[] = [];
</script>

{#if alerts.length}<div class="alert-stack">
    {#each alerts as alert}<div class="alert-strip {alert.level ?? alert.severity ?? 'info'}">
        <Icon name="warning" size={17} />
        <div>
          {#if alert.title}<strong>{alert.title}</strong>{/if}
          <span>{alert.message ?? 'Attention required'}</span>
        </div>
      </div>{/each}
  </div>{/if}
