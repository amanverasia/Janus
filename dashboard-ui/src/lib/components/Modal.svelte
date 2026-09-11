<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import Icon from './Icon.svelte';

  export let open = false;
  export let title = '';
  export let description = '';
  export let wide = false;
  const dispatch = createEventDispatcher<{ close: void }>();
  let dialog: HTMLDialogElement;
  // Unique per instance: a shared id collides the moment a page has two modals.
  const titleId = `modal-title-${Math.random().toString(36).slice(2, 10)}`;
  // Only dismiss on a click that both started and ended on the backdrop, so
  // selecting text inside the modal and releasing outside does not close it.
  let pressedBackdrop = false;

  $: if (dialog) {
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }

  function close() {
    dispatch('close');
  }
  function backdropDown(event: MouseEvent) {
    pressedBackdrop = event.target === dialog;
  }
  function backdropUp(event: MouseEvent) {
    if (pressedBackdrop && event.target === dialog) close();
    pressedBackdrop = false;
  }
  onMount(() => () => {
    if (dialog?.open) dialog.close();
  });
</script>

<dialog
  bind:this={dialog}
  class:wide
  aria-labelledby={titleId}
  on:close={close}
  on:mousedown={backdropDown}
  on:mouseup={backdropUp}
>
  <div class="modal-card">
    <header>
      <div>
        <h2 id={titleId}>{title}</h2>
        {#if description}<p>{description}</p>{/if}
      </div>
      <button class="icon-button" type="button" aria-label="Close dialog" on:click={close}>
        <Icon name="x" />
      </button>
    </header>
    <div class="modal-body"><slot /></div>
  </div>
</dialog>
