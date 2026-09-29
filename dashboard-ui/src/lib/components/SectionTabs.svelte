<script lang="ts">
  import type { NavHub, NavItem } from '$lib/nav';

  export let hub: NavHub | undefined;
  export let active: NavItem;
  export let navigate: (href: string) => void;

  function follow(event: MouseEvent, href: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    navigate(href);
  }
</script>

{#if hub && hub.tabs.length > 1}
  <nav class="section-tabs" aria-label={`${hub.label} sections`}>
    {#each hub.tabs as tab (tab.href)}<a
        href={tab.href}
        class:active={tab.href === active.href}
        aria-current={tab.href === active.href ? 'page' : undefined}
        on:click={(event) => follow(event, tab.href)}
      >
        {tab.label}
      </a>{/each}
  </nav>
{/if}
