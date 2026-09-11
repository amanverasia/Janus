<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { ToastItem } from '$lib/types';
  import Icon from './Icon.svelte';
  export let toasts: ToastItem[] = [];
  const dispatch = createEventDispatcher<{ dismiss: number }>();
</script>

<!-- aria-atomic="false": re-reading the whole stack on every change made the
     screen-reader experience unusable once more than one toast was up. -->
<div class="toasts" aria-live="polite" aria-atomic="false">
  {#each toasts as toast (toast.id)}
    <div class="toast {toast.kind}" role={toast.kind === 'error' ? 'alert' : 'status'}>
      <span class="toast-icon">
        <Icon name={toast.kind === 'error' ? 'warning' : 'check'} size={16} />
      </span>
      <span class="toast-message">{toast.message}</span>
      <button
        class="toast-dismiss"
        type="button"
        aria-label="Dismiss notification"
        on:click={() => dispatch('dismiss', toast.id)}
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  {/each}
</div>

<style>
  .toast-message {
    flex: 1;
    min-width: 0;
  }
  .toast-dismiss {
    flex: none;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    margin: -2px -4px -2px 0;
    padding: 0;
    border: 0;
    border-radius: 7px;
    color: var(--faint);
    background: transparent;
    cursor: pointer;
  }
  .toast-dismiss:hover {
    color: var(--text);
    background: var(--surface-soft);
  }
</style>
