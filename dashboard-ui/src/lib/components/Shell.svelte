<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { bool, object, text } from '$lib/data';
  import { navGroups, type NavItem } from '$lib/nav';
  import type { JsonObject } from '$lib/types';
  import Icon from './Icon.svelte';
  import CommandPalette from './CommandPalette.svelte';

  export let active: NavItem;
  export let loading = false;
  export let health: JsonObject | null = null;
  export let identity = '';
  let mobileOpen = false;
  let paletteOpen = false;
  let themeMode: 'system' | 'light' | 'dark' = 'system';
  const dispatch = createEventDispatcher<{ navigate: string; refresh: void; logout: void }>();

  $: healthInfo = object(health);
  $: healthStatus = health === null ? 'unknown' : text(healthInfo.status, 'unknown');
  $: schedulerInfo = object(healthInfo.schedulers);
  $: staleNotes = [
    bool(object(schedulerInfo.inventory).stale)
      ? `Inventory check ${text(object(schedulerInfo.inventory).last_check_ago) || 'never run'}`
      : '',
    bool(object(schedulerInfo.pricing).stale)
      ? `Pricing sync ${text(object(schedulerInfo.pricing).last_sync_ago) || 'never run'}`
      : ''
  ].filter((note) => note.length > 0);
  $: dotClass =
    healthStatus === 'offline'
      ? 'offline'
      : healthStatus === 'degraded'
        ? 'degraded'
        : healthStatus === 'ok' && staleNotes.length
          ? 'stale'
          : healthStatus === 'ok'
            ? ''
            : 'unknown';
  $: healthTitle =
    healthStatus === 'ok'
      ? 'System online'
      : healthStatus === 'degraded'
        ? 'System degraded'
        : healthStatus === 'offline'
          ? 'System offline'
          : 'Checking system…';
  $: healthDetail =
    healthStatus === 'ok'
      ? staleNotes.length
        ? `Stale: ${staleNotes.join(' · ')}`
        : 'All components healthy'
      : healthStatus === 'degraded'
        ? 'Providers or schedulers need attention'
        : healthStatus === 'offline'
          ? 'Database unreachable'
          : 'Connecting to control plane';
  $: identityLabel = identity || 'Signed in';
  $: initials =
    (
      identityLabel
        .replace(/[^A-Za-z0-9]/g, ' ')
        .trim()
        .split(/\s+/)
        .filter(Boolean)[0] ?? ''
    )
      .slice(0, 2)
      .toUpperCase() || '··';

  onMount(() => {
    const saved = localStorage.getItem('janus-theme');
    themeMode = saved === 'light' || saved === 'dark' ? saved : 'system';
    const listener = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        paletteOpen = !paletteOpen;
      }
      if (
        event.key === '/' &&
        !(event.target instanceof HTMLInputElement) &&
        !(event.target instanceof HTMLTextAreaElement)
      ) {
        event.preventDefault();
        paletteOpen = true;
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  });

  function navigate(href: string) {
    mobileOpen = false;
    paletteOpen = false;
    dispatch('navigate', href);
  }
  function theme() {
    themeMode = themeMode === 'system' ? 'light' : themeMode === 'light' ? 'dark' : 'system';
    if (themeMode === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = themeMode;
    localStorage.setItem('janus-theme', themeMode);
  }
</script>

<svelte:head><title>{active.label} · Janus</title></svelte:head>
<div class="app-shell">
  {#if mobileOpen}<button
      class="nav-scrim"
      aria-label="Close navigation"
      on:click={() => (mobileOpen = false)}
    ></button>{/if}
  <aside class:open={mobileOpen} class="sidebar">
    <div class="brand">
      <div class="brand-mark">
        <span></span>
        <span></span>
        <span></span>
        <span></span>
      </div>
      <div>
        <strong>Janus</strong>
        <small>Gateway control plane</small>
      </div>
      <button
        class="mobile-close icon-button"
        on:click={() => (mobileOpen = false)}
        aria-label="Close navigation"
      >
        <Icon name="x" />
      </button>
    </div>
    <nav aria-label="Main navigation">
      {#each navGroups as group}
        <div class="nav-group">
          <span class="nav-label">{group.label}</span>
          {#each group.items as item}<a
              href={item.href}
              class:active={item.section === active.section ||
                (item.section === 'inventory' && active.section === 'inventory-keys')}
              aria-current={item.section === active.section ? 'page' : undefined}
              on:click|preventDefault={() => navigate(item.href)}
            >
              <span class="nav-icon"><Icon name={item.icon} /></span>
              <span>{item.label}</span>
            </a>{/each}
        </div>
      {/each}
    </nav>
    <div class="sidebar-footer">
      <div class="system-dot {dotClass}"></div>
      <div>
        <strong>{healthTitle}</strong>
        <small>{healthDetail}</small>
      </div>
    </div>
  </aside>

  <main class="main-shell">
    <header class="topbar">
      <button
        class="icon-button mobile-menu"
        on:click={() => (mobileOpen = true)}
        aria-label="Open navigation"
      >
        <Icon name="menu" />
      </button>
      <button class="command-trigger" on:click={() => (paletteOpen = true)}>
        <Icon name="search" size={17} />
        <span>Search dashboard…</span>
        <kbd>⌘ K</kbd>
      </button>
      <div class="top-actions">
        <button
          class="icon-button"
          on:click={() => dispatch('refresh')}
          aria-label="Refresh data"
          class:spinning={loading}
        >
          <Icon name="refresh" />
        </button>
        <button
          class="icon-button"
          on:click={theme}
          title={`Theme: ${themeMode}`}
          aria-label={`Theme is ${themeMode}; switch theme`}
        >
          <Icon name={themeMode === 'light' ? 'moon' : themeMode === 'dark' ? 'sun' : 'settings'} />
        </button>
        <button class="profile" on:click={() => dispatch('logout')} aria-label="Log out">
          <span>{initials}</span>
          <div>
            <strong>{identityLabel}</strong>
            <small>Log out</small>
          </div>
          <Icon name="logout" size={16} />
        </button>
      </div>
    </header>
    <div class="content"><slot /></div>
  </main>
</div>
<CommandPalette
  open={paletteOpen}
  on:close={() => (paletteOpen = false)}
  on:navigate={(e) => navigate(e.detail)}
/>
