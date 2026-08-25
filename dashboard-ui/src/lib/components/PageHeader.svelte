<script lang="ts">
  export let eyebrow = 'Janus control plane';
  export let title = '';
  export let description = '';
</script>

<header class="page-header">
  <div>
    <span class="eyebrow">{eyebrow}</span>
    <h1>{title}</h1>
    {#if description}<p>{description}</p>{/if}
  </div>
  <div class="page-actions"><slot /></div>
</header>
