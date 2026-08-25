<script lang="ts">
  import type { ToastItem } from '$lib/types';
  import Icon from './Icon.svelte';
  export let toasts: ToastItem[] = [];
</script>

<div class="toasts" aria-live="polite" aria-atomic="true">
  {#each toasts as toast (toast.id)}
    <div class="toast {toast.kind}" role={toast.kind === 'error' ? 'alert' : 'status'}>
      <span class="toast-icon">
        <Icon name={toast.kind === 'error' ? 'warning' : 'check'} size={16} />
      </span>
      <span>{toast.message}</span>
    </div>
  {/each}
</div>
