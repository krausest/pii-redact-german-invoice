<script lang="ts">
  import Icon from './icons/Icon.svelte'
  import FileDrop from './FileDrop.svelte'
  import Settings from './Settings.svelte'
  import { t } from './i18n.svelte'
  import type { Dpi, Tool } from './types'

  let {
    tool = $bindable(),
    current,
    total,
    ongoto,
    canDelete = false,
    busy = false,
    rendering = false,
    downloadLabel,
    dpi,
    unwarp,
    dpiDisabled = false,
    onDpiChange,
    onUnwarpChange,
    onDelete,
    onDownload,
    onSelectFile,
    onFileError,
  }: {
    tool: Tool
    current: number
    total: number
    ongoto: (index: number) => void
    canDelete?: boolean
    busy?: boolean
    rendering?: boolean
    /** Required, not defaulted — a default would bake English into the component. */
    downloadLabel: string
    dpi: Dpi
    unwarp: boolean
    dpiDisabled?: boolean
    onDpiChange: (dpi: Dpi) => void
    onUnwarpChange: (unwarp: boolean) => void
    onDelete: () => void
    onDownload: () => void
    onSelectFile: (file: File) => void
    onFileError?: (message: string) => void
  } = $props()

  const m = $derived(t())
</script>

<div class="toolbar">
  <div class="group" role="group" aria-label={m.toolbar.tools}>
    <button
      class:active={tool === 'select'}
      onclick={() => (tool = 'select')}
      disabled={busy}
      title={m.toolbar.select}
      aria-label={m.toolbar.select}
    >
      <Icon name="pointer" size={18} />
    </button>
    <button
      class:active={tool === 'draw'}
      onclick={() => (tool = 'draw')}
      disabled={busy}
      title={m.toolbar.drawBox}
      aria-label={m.toolbar.drawBox}
    >
      <Icon name="draw-box" size={18} />
    </button>
    <button onclick={onDelete} disabled={!canDelete || busy} title={m.toolbar.delete} aria-label={m.toolbar.deleteSelected}>
      <Icon name="trash" size={18} />
    </button>
  </div>

  {#if total > 1}
    <div class="pagenav" role="group" aria-label={m.toolbar.pageNavigation}>
      <button onclick={() => ongoto(current - 1)} disabled={current <= 0 || busy} title={m.toolbar.previousPage} aria-label={m.toolbar.previousPage}>
        <Icon name="chevron-left" size={16} />
      </button>
      <span>{current + 1} / {total}</span>
      <button onclick={() => ongoto(current + 1)} disabled={current >= total - 1 || busy} title={m.toolbar.nextPage} aria-label={m.toolbar.nextPage}>
        <Icon name="chevron-right" size={16} />
      </button>
    </div>
  {/if}

  <Settings
    variant="inline"
    {dpi}
    {unwarp}
    {dpiDisabled}
    {onDpiChange}
    {onUnwarpChange}
    disabled={busy}
  />

  <div class="spacer"></div>

  <FileDrop variant="inline" onselect={onSelectFile} onerror={onFileError} disabled={busy} />

  <button class="primary" onclick={onDownload} disabled={busy} title={downloadLabel} aria-label={downloadLabel}>
    {#if rendering}
      <span class="spinner" aria-hidden="true"></span>
    {:else}
      <Icon name="download" size={16} />
    {/if}
    {rendering ? m.toolbar.rendering : m.toolbar.download}
  </button>
</div>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin-bottom: 0.9rem;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
    padding: 0.55rem 0.65rem;
  }
  .group {
    display: inline-flex;
    gap: 0.15rem;
    background: var(--bg);
    border-radius: 10px;
    padding: 0.2rem;
  }
  .pagenav {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--muted);
    font-size: 0.85rem;
    padding: 0 0.25rem;
  }
  .pagenav span {
    min-width: 3.2rem;
    text-align: center;
  }
  .spacer {
    flex: 1;
  }
  button {
    font: inherit;
    font-weight: 600;
    padding: 0.45rem 0.6rem;
    border-radius: 8px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--fg);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .group button,
  .pagenav button {
    padding: 0.4rem;
  }
  button:hover:not(:disabled) {
    background: color-mix(in srgb, var(--fg) 6%, transparent);
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }
  button.active {
    background: var(--card);
    color: var(--accent);
    box-shadow: var(--shadow);
  }
  button.primary {
    background: var(--accent);
    color: var(--accent-fg);
    border-color: transparent;
    padding: 0.5rem 0.9rem;
  }
  button.primary:hover:not(:disabled) {
    filter: brightness(1.08);
  }
  .spinner {
    width: 13px;
    height: 13px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
