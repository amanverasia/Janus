<script lang="ts">
  import Icon from './Icon.svelte';
  import type { IconName } from '$lib/nav';
  export let icon: IconName = 'layers';
  export let title = 'Nothing here yet';
  export let message = 'New data will appear here when it becomes available.';
</script>

<div class="empty-state">
  <span class="empty-icon"><Icon name={icon} size={22} /></span>
  <h3>{title}</h3>
  <p>{message}</p>
  <slot />
</div>
