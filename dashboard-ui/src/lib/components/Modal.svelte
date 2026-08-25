<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import Icon from './Icon.svelte';

  export let open = false;
  export let title = '';
  export let description = '';
  export let wide = false;
  const dispatch = createEventDispatcher<{ close: void }>();
  let dialog: HTMLDialogElement;

  $: if (dialog) {
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }

  function close() {
    dispatch('close');
  }
  function backdrop(event: MouseEvent) {
    if (event.target === dialog) close();
  }
  onMount(() => () => {
    if (dialog?.open) dialog.close();
  });
</script>

<dialog
  bind:this={dialog}
  class:wide
  aria-labelledby="modal-title"
  on:close={close}
  on:click={backdrop}
>
  <div class="modal-card">
    <header>
      <div>
        <h2 id="modal-title">{title}</h2>
        {#if description}<p>{description}</p>{/if}
      </div>
      <button class="icon-button" type="button" aria-label="Close dialog" on:click={close}>
        <Icon name="x" />
      </button>
    </header>
    <div class="modal-body"><slot /></div>
  </div>
</dialog>
