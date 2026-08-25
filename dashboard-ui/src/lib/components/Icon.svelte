<script lang="ts">
  import type { IconName } from '$lib/nav';
  export let name:
    | IconName
    | 'menu'
    | 'search'
    | 'x'
    | 'refresh'
    | 'arrow'
    | 'check'
    | 'warning'
    | 'plus'
    | 'trash'
    | 'download'
    | 'sun'
    | 'moon'
    | 'logout'
    | 'eye'
    | 'edit'
    | 'more' = 'home';
  export let size = 18;
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === 'home'}<path d="m3 11 9-8 9 8" />
    <path d="M5 10v10h14V10M9 20v-6h6v6" />
  {:else if name === 'pulse'}<path d="M3 12h4l2-7 4 14 2-7h6" />
  {:else if name === 'chart'}<path d="M4 19V9m6 10V5m6 14v-7m5 7H2" />
  {:else if name === 'trophy'}<path d="M8 4h8v5a4 4 0 0 1-8 0V4Z" />
    <path d="M8 6H4v2a4 4 0 0 0 4 4m8-6h4v2a4 4 0 0 1-4 4m-4 1v4m-4 3h8m-6-3h4" />
  {:else if name === 'logs'}<path d="M5 5h14M5 12h14M5 19h10" />
    <circle cx="2" cy="5" r=".5" fill="currentColor" />
    <circle cx="2" cy="12" r=".5" fill="currentColor" />
    <circle cx="2" cy="19" r=".5" fill="currentColor" />
  {:else if name === 'vault'}<rect x="3" y="4" width="18" height="16" rx="3" />
    <circle cx="12" cy="12" r="4" />
    <path d="M12 8v2m4 2h-2m-2 4v-2m-4-2h2" />
  {:else if name === 'plug'}<path
      d="m8 12 8-8m-3 1 6 6m-9-2 5 5-3 3a3.5 3.5 0 0 1-5-5l3-3Zm-4 8-3 3"
    />
  {:else if name === 'route'}<circle cx="6" cy="6" r="2" />
    <circle cx="18" cy="18" r="2" />
    <path d="M8 6h4a4 4 0 0 1 4 4v2m0 0-3-3m3 3 3-3M8 18h8" />
  {:else if name === 'spark'}<path
      d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"
    />
    <path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z" />
  {:else if name === 'wallet'}<path
      d="M4 6h14a2 2 0 0 1 2 2v10H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12"
    />
    <path d="M20 10h-5a2 2 0 0 0 0 4h5" />
    <circle cx="15" cy="12" r=".5" fill="currentColor" />
  {:else if name === 'key'}<circle cx="8" cy="15" r="4" />
    <path d="m11 12 9-9m-4 4 3 3m-6 0 3 3" />
  {:else if name === 'tool'}<path
      d="M14 6a4 4 0 0 0-5-4l2.5 2.5-3 3L6 5a4 4 0 0 0 4 5L3 17l4 4 7-7a4 4 0 0 0 5-5l-2.5 2.5-3-3L16 6Z"
    />
  {:else if name === 'tag'}<path d="M3 12V4h8l10 10-7 7L3 12Z" />
    <circle cx="8" cy="8" r="1" />
  {:else if name === 'settings'}<circle cx="12" cy="12" r="3" />
    <path
      d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"
    />
  {:else if name === 'layers'}<path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 12 9 5 9-5m-18 4 9 5 9-5" />
  {:else if name === 'menu'}<path d="M4 7h16M4 12h16M4 17h16" />
  {:else if name === 'search'}<circle cx="11" cy="11" r="7" />
    <path d="m20 20-4-4" />
  {:else if name === 'x'}<path d="m5 5 14 14M19 5 5 19" />
  {:else if name === 'refresh'}<path d="M20 7v5h-5M4 17v-5h5" />
    <path d="M18 12a6 6 0 0 0-10-4L4 12m2 0a6 6 0 0 0 10 4l4-4" />
  {:else if name === 'arrow'}<path d="m9 18 6-6-6-6" />
  {:else if name === 'check'}<path d="m5 12 4 4L19 6" />
  {:else if name === 'warning'}<path d="M12 3 2 21h20L12 3Z" />
    <path d="M12 9v5m0 3v.01" />
  {:else if name === 'plus'}<path d="M12 5v14M5 12h14" />
  {:else if name === 'trash'}<path d="M4 7h16m-10 4v6m4-6v6M7 7l1 14h8l1-14m-8-3h6l1 3H8l1-3Z" />
  {:else if name === 'download'}<path d="M12 3v12m-4-4 4 4 4-4M4 20h16" />
  {:else if name === 'sun'}<circle cx="12" cy="12" r="4" />
    <path
      d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
    />
  {:else if name === 'moon'}<path d="M20 15.5A8 8 0 0 1 8.5 4 8 8 0 1 0 20 15.5Z" />
  {:else if name === 'logout'}<path d="M10 4H4v16h6m5-4 4-4-4-4m4 4H9" />
  {:else if name === 'eye'}<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
    <circle cx="12" cy="12" r="2.5" />
  {:else if name === 'edit'}<path d="m14 5 5 5M4 20l3.5-.7L19 7.8 16.2 5 4.7 16.5 4 20Z" />
  {:else if name === 'more'}<circle cx="5" cy="12" r="1" fill="currentColor" />
    <circle cx="12" cy="12" r="1" fill="currentColor" />
    <circle cx="19" cy="12" r="1" fill="currentColor" />
  {/if}
</svg>
