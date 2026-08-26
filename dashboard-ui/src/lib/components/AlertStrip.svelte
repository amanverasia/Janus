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
          <span>{alert.message ?? alert.detail ?? 'Attention required'}</span>
        </div>
        {#if alert.href}<a class="alert-link" href={alert.href}>View</a>{/if}
      </div>{/each}
  </div>{/if}
