<script lang="ts">
  export let label = 'page';
  export let section = 'overview';
  export let pathname = '/dashboard/ui';

  const statSections = new Set([
    'overview',
    'usage',
    'analytics',
    'inventory',
    'routing',
    'budgets'
  ]);
  const singlePanelSections = new Set([
    'leaderboard',
    'request-logs',
    'inventory-keys',
    'providers',
    'models',
    'combos',
    'savers',
    'keys',
    'tools',
    'pricing',
    'settings'
  ]);

  $: inventoryForm =
    section === 'inventory' && (pathname.endsWith('/add') || pathname.endsWith('/import'));
  $: statCount = statSections.has(section) && !inventoryForm ? 4 : 0;
  $: showSecondaryPanel = !inventoryForm && !singlePanelSections.has(section);
</script>

<div class="page-skeleton" role="status" aria-label={`Loading ${label}`} aria-busy="true">
  <span class="sr-only">Loading {label}…</span>
  <header class="skeleton-header" aria-hidden="true">
    <div>
      <span class="skeleton-line eyebrow-line"></span>
      <span class="skeleton-line title-line"></span>
      <span class="skeleton-line copy-line"></span>
    </div>
    <span class="skeleton-button"></span>
  </header>
  {#if statCount}
    <div class="skeleton-stats" aria-hidden="true">
      {#each Array(statCount) as _}
        <div class="skeleton-card">
          <span class="skeleton-line short-line"></span>
          <span class="skeleton-line value-line"></span>
          <span class="skeleton-line detail-line"></span>
        </div>
      {/each}
    </div>
  {/if}
  <div class:single={!showSecondaryPanel} class="skeleton-panels" aria-hidden="true">
    <div class="skeleton-panel skeleton-panel-wide">
      <span class="skeleton-line panel-title-line"></span>
      {#each Array(5) as _}<span class="skeleton-row"></span>{/each}
    </div>
    {#if showSecondaryPanel}
      <div class="skeleton-panel">
        <span class="skeleton-line panel-title-line"></span>
        {#each Array(4) as _}<span class="skeleton-row"></span>{/each}
      </div>
    {/if}
  </div>
</div>
