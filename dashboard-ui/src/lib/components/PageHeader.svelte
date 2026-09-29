<script lang="ts">
  export let eyebrow = '';
  export let title = '';
  export let description = '';
</script>

<header class="page-header">
  <div>
    {#if eyebrow}<span class="eyebrow">{eyebrow}</span>{/if}
    <h1>{title}</h1>
    {#if description}<p>{description}</p>{/if}
  </div>
  <div class="page-actions"><slot /></div>
</header>
