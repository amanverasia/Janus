<script lang="ts">
  import { afterUpdate, onMount } from 'svelte';
  import type { NavHub, NavItem } from '#lib/nav.ts';

  export let hub: NavHub | undefined;
  export let active: NavItem;
  export let navigate: (href: string) => void;

  let strip: HTMLElement | undefined;
  let canScrollStart = false;
  let canScrollEnd = false;

  function measure() {
    if (!strip) return;
    const overflow = strip.scrollWidth - strip.clientWidth;
    canScrollStart = strip.scrollLeft > 2;
    canScrollEnd = overflow > 2 && strip.scrollLeft < overflow - 2;
  }

  let revealedHref = '';

  function revealActive() {
    const current = strip?.querySelector<HTMLElement>('a[aria-current="page"]');
    if (strip && current && strip.scrollWidth > strip.clientWidth) {
      strip.scrollLeft = Math.max(0, current.offsetLeft - 24);
    }
    measure();
  }

  onMount(() => {
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  });

  afterUpdate(() => {
    if (!strip || active.href === revealedHref) return;
    revealedHref = active.href;
    revealActive();
  });

  function follow(event: MouseEvent, href: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    navigate(href);
  }
</script>

{#if hub && hub.tabs.length > 1}
  <div class="section-tabs-wrap" class:fade-start={canScrollStart} class:fade-end={canScrollEnd}>
    <nav
      class="section-tabs"
      aria-label={`${hub.label} sections`}
      bind:this={strip}
      on:scroll={measure}
    >
      {#each hub.tabs as tab (tab.href)}<a
          href={tab.href}
          class:active={tab.href === active.href}
          aria-current={tab.href === active.href ? 'page' : undefined}
          on:click={(event) => follow(event, tab.href)}
        >
          {tab.label}
        </a>{/each}
    </nav>
  </div>
{/if}
