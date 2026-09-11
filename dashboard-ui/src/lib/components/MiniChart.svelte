<script lang="ts">
  export let values: number[] = [];
  export let label = 'Trend';
  export let height = 130;
  export let emptyMessage = 'No data for this period yet.';

  // Unique per instance: a shared gradient id breaks every chart but the first
  // once two charts share a page.
  const gradientId = `mini-chart-${Math.random().toString(36).slice(2, 10)}`;

  $: hasData = values.length > 1 && values.some((value) => Number.isFinite(value) && value !== 0);
  $: safe = hasData ? values.filter((value) => Number.isFinite(value)) : [];
  $: max = safe.length ? Math.max(...safe, 1) : 1;
  $: min = safe.length ? Math.min(...safe, 0) : 0;
  $: range = max - min || 1;
  $: points = safe
    .map(
      (v, i) =>
        `${(i / (safe.length - 1)) * 100},${height - 12 - ((v - min) / range) * (height - 24)}`
    )
    .join(' ');
  $: area = `0,${height} ${points} 100,${height}`;
</script>

{#if hasData}
  <svg
    class="mini-chart"
    viewBox={`0 0 100 ${height}`}
    preserveAspectRatio="none"
    role="img"
    aria-label={label}
  >
    <defs>
      <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--accent)" stop-opacity=".28" />
        <stop offset="1" stop-color="var(--accent)" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path
      d={`M0 ${height * 0.25}H100 M0 ${height * 0.5}H100 M0 ${height * 0.75}H100`}
      class="gridline"
    />
    <polygon points={area} fill={`url(#${gradientId})`} />
    <polyline
      {points}
      fill="none"
      stroke="var(--accent)"
      stroke-width="2"
      vector-effect="non-scaling-stroke"
    />
  </svg>
{:else}
  <!-- A flat baseline reads as real data. Say there is none instead. -->
  <div class="mini-chart-empty" style={`min-height:${height}px`} role="img" aria-label={label}>
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      <path d="M0 20H100 M0 8H100 M0 32H100" class="gridline" />
    </svg>
    <span>{emptyMessage}</span>
  </div>
{/if}

<style>
  .mini-chart-empty {
    position: relative;
    display: grid;
    place-items: center;
    margin-top: 12px;
  }
  .mini-chart-empty svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    opacity: 0.55;
  }
  .mini-chart-empty span {
    position: relative;
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--surface-soft);
    color: var(--muted);
    font-size: 11px;
  }
</style>
