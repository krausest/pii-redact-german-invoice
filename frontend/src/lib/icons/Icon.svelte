<script module lang="ts">
  export type IconName =
    | 'pointer'
    | 'draw-box'
    | 'trash'
    | 'chevron-left'
    | 'chevron-right'
    | 'upload'
    | 'download'
</script>

<script lang="ts">
  let { name, size = 20 }: { name: IconName; size?: number } = $props()
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.5"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === 'pointer'}
    <path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z" />
    <path d="M13 13l6 6" />
  {:else if name === 'draw-box'}
    <rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 3" />
  {:else if name === 'trash'}
    <path d="M4 7h16" />
    <path d="M9 7V4h6v3" />
    <path d="M6 7l1 13a2 2 0 002 2h6a2 2 0 002-2l1-13" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
  {:else if name === 'chevron-left'}
    <path d="M15 18l-6-6 6-6" />
  {:else if name === 'chevron-right'}
    <path d="M9 6l6 6-6 6" />
  {:else if name === 'upload'}
    <path d="M12 16V4m0 0L8 8m4-4l4 4" />
    <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
  {:else if name === 'download'}
    <path d="M12 4v12m0 0l-4-4m4 4l4-4" />
    <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
  {/if}
</svg>
