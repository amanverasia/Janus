<script lang="ts">
  export let label = '';
  export let value = '—';
  export let detail = '';
  export let tone: 'cyan' | 'teal' | 'violet' | 'amber' = 'cyan';
</script>

<article class="stat-card {tone}">
  <div class="stat-glow"></div>
  <span class="eyebrow">{label}</span>
  <strong>{value}</strong>
  {#if detail}<span class="stat-detail">{detail}</span>{/if}
</article>
