<script lang="ts">
  import { createEventDispatcher, tick } from 'svelte';
  import { commandItems } from '$lib/nav';
  import Icon from './Icon.svelte';

  export let open = false;
  let query = '';
  let input: HTMLInputElement;
  const dispatch = createEventDispatcher<{ close: void; navigate: string }>();
  $: results = commandItems.filter((item) =>
    `${item.label} ${item.keywords ?? ''}`.toLowerCase().includes(query.toLowerCase())
  );
  $: if (open) tick().then(() => input?.focus());
  $: if (!open) query = '';

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape') dispatch('close');
    if (event.key === 'Enter' && results[0]) dispatch('navigate', results[0].href);
  }
</script>

{#if open}
  <div
    class="palette-backdrop"
    role="presentation"
    on:click={() => dispatch('close')}
    on:keydown={keydown}
  >
    <div
      class="palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      tabindex="-1"
      on:click|stopPropagation
      on:keydown|stopPropagation
    >
      <div class="palette-search">
        <Icon name="search" size={20} />
        <input
          bind:this={input}
          bind:value={query}
          on:keydown={keydown}
          placeholder="Search pages and actions…"
          aria-label="Search commands"
        />
        <kbd>Esc</kbd>
      </div>
      <div class="palette-results">
        <span class="eyebrow">Navigate</span>
        {#each results as item}
          <button type="button" on:click={() => dispatch('navigate', item.href)}>
            <span class="nav-icon"><Icon name={item.icon} /></span>
            <span>{item.label}</span>
            <Icon name="arrow" size={15} />
          </button>
        {:else}<div class="palette-empty">No matching pages</div>{/each}
      </div>
      <footer>
        <span>
          <kbd>↵</kbd>
          open
        </span>
        <span>
          <kbd>⌘ K</kbd>
          toggle
        </span>
      </footer>
    </div>
  </div>
{/if}
