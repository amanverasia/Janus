<script lang="ts">
  export let values: number[] = [];
  export let label = 'Trend';
  export let height = 130;
  $: safe = values.length > 1 ? values : [0, 0];
  $: max = Math.max(...safe, 1);
  $: min = Math.min(...safe, 0);
  $: range = max - min || 1;
  $: points = safe
    .map(
      (v, i) =>
        `${(i / (safe.length - 1)) * 100},${height - 12 - ((v - min) / range) * (height - 24)}`
    )
    .join(' ');
  $: area = `0,${height} ${points} 100,${height}`;
</script>

<svg
  class="mini-chart"
  viewBox={`0 0 100 ${height}`}
  preserveAspectRatio="none"
  role="img"
  aria-label={label}
>
  <defs>
    <linearGradient id="cloudline-chart" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="var(--accent)" stop-opacity=".28" />
      <stop offset="1" stop-color="var(--accent)" stop-opacity="0" />
    </linearGradient>
  </defs>
  <path
    d={`M0 ${height * 0.25}H100 M0 ${height * 0.5}H100 M0 ${height * 0.75}H100`}
    class="gridline"
  />
  <polygon points={area} fill="url(#cloudline-chart)" />
  <polyline
    {points}
    fill="none"
    stroke="var(--accent)"
    stroke-width="2"
    vector-effect="non-scaling-stroke"
  />
</svg>
