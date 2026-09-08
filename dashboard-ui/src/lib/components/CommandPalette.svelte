<script lang="ts">
  import { createEventDispatcher, tick } from 'svelte';
  import { commandItems } from '$lib/nav';
  import Icon from './Icon.svelte';

  export let open = false;
  let query = '';
  let activeIndex = 0;
  let input: HTMLInputElement;
  const dispatch = createEventDispatcher<{ close: void; navigate: string }>();
  $: results = commandItems.filter((item) =>
    `${item.label} ${item.keywords ?? ''}`.toLowerCase().includes(query.toLowerCase())
  );
  $: activeIndex = Math.min(activeIndex, Math.max(results.length - 1, 0));
  $: if (open) tick().then(() => input?.focus());
  $: if (!open) {
    query = '';
    activeIndex = 0;
  }
  $: if (query) activeIndex = 0;

  function optionId(index: number): string {
    return `palette-option-${index}`;
  }

  function moveActive(delta: number) {
    if (!results.length) return;
    activeIndex = (activeIndex + delta + results.length) % results.length;
    document.getElementById(optionId(activeIndex))?.scrollIntoView({ block: 'nearest' });
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      dispatch('close');
      return;
    }
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        moveActive(1);
        break;
      case 'ArrowUp':
        event.preventDefault();
        moveActive(-1);
        break;
      case 'Home':
        event.preventDefault();
        activeIndex = 0;
        document.getElementById(optionId(0))?.scrollIntoView({ block: 'nearest' });
        break;
      case 'End':
        event.preventDefault();
        activeIndex = Math.max(results.length - 1, 0);
        document.getElementById(optionId(activeIndex))?.scrollIntoView({ block: 'nearest' });
        break;
      case 'Enter': {
        const item = results[activeIndex];
        if (item) {
          event.preventDefault();
          dispatch('navigate', item.href);
        }
        break;
      }
    }
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
          on:input={() => (activeIndex = 0)}
          placeholder="Search pages and actions…"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-results-listbox"
          aria-autocomplete="list"
          aria-activedescendant={results.length ? optionId(activeIndex) : undefined}
          aria-label="Search commands"
        />
        <kbd>Esc</kbd>
      </div>
      <div
        class="palette-results"
        id="palette-results-listbox"
        role="listbox"
        aria-label="Pages and actions"
      >
        <span class="eyebrow">Navigate</span>
        {#each results as item, index}
          <button
            type="button"
            id={optionId(index)}
            role="option"
            aria-selected={index === activeIndex}
            class:active={index === activeIndex}
            on:click={() => dispatch('navigate', item.href)}
            on:focus={() => (activeIndex = index)}
            on:mouseenter={() => (activeIndex = index)}
          >
            <span class="nav-icon"><Icon name={item.icon} /></span>
            <span>{item.label}</span>
            <Icon name="arrow" size={15} />
          </button>
        {:else}<div class="palette-empty">No matching pages</div>{/each}
      </div>
      <footer>
        <span>
          <kbd>↑↓</kbd>
          navigate
        </span>
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
