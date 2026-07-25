<script lang="ts">
  import Icon from './icons/Icon.svelte'
  import { ACCEPT_ATTR, pickUpload } from './upload'

  let {
    onselect,
    onerror,
    disabled = false,
    variant = 'panel',
  }: {
    onselect: (file: File) => void
    onerror?: (message: string) => void
    disabled?: boolean
    /** `panel`: full-width idle target. `inline`: toolbar-sized "new upload" control. */
    variant?: 'panel' | 'inline'
  } = $props()

  let dragging = $state(false)
  let inputEl: HTMLInputElement | undefined

  function pick(file: File | undefined | null) {
    pickUpload(file, { disabled, onSelect: onselect, onError: onerror })
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    dragging = false
    if (disabled) return
    pick(e.dataTransfer?.files?.[0])
  }

  function onDragOver(e: DragEvent) {
    e.preventDefault()
    if (!disabled) dragging = true
  }

  function onChange(e: Event) {
    const target = e.target as HTMLInputElement
    pick(target.files?.[0])
    target.value = '' // allow re-selecting the same file
  }
</script>

<div
  class="filedrop filedrop--{variant}"
  class:dragging
  class:disabled
  role="button"
  tabindex="0"
  aria-disabled={disabled}
  title={variant === 'inline' ? 'Upload a new file — click or drop' : undefined}
  onclick={() => !disabled && inputEl?.click()}
  onkeydown={(e) => {
    if ((e.key === 'Enter' || e.key === ' ') && !disabled) {
      e.preventDefault()
      inputEl?.click()
    }
  }}
  ondrop={onDrop}
  ondragover={onDragOver}
  ondragleave={() => (dragging = false)}
>
  <input bind:this={inputEl} type="file" accept={ACCEPT_ATTR} hidden onchange={onChange} {disabled} />
  {#if variant === 'panel'}
    <span class="icon-wrap">
      <Icon name="upload" size={40} />
    </span>
    <p><strong>Drag &amp; drop</strong> a PNG, JPEG, or PDF here</p>
    <p class="hint">or click to choose a file</p>
  {:else}
    <span class="icon-wrap">
      <Icon name="upload" size={18} />
    </span>
    New upload
  {/if}
</div>

<style>
  .filedrop {
    border-style: dashed;
    border-color: var(--border);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s, color 0.15s;
  }
  .filedrop:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .filedrop:hover:not(.disabled),
  .filedrop.dragging {
    border-color: var(--accent);
  }
  .filedrop.disabled {
    cursor: not-allowed;
  }
  .icon-wrap {
    display: inline-flex;
  }

  /* Full-width target shown while no document is loaded. */
  .filedrop--panel {
    border-width: 2px;
    border-radius: 12px;
    padding: 2.5rem 1.5rem;
    text-align: center;
    color: var(--muted);
    background: var(--card);
  }
  .filedrop--panel:hover:not(.disabled),
  .filedrop--panel.dragging {
    color: var(--fg);
  }
  .filedrop--panel.dragging {
    background: color-mix(in srgb, var(--accent) 8%, var(--card));
  }
  .filedrop--panel.disabled {
    opacity: 0.55;
  }
  .filedrop--panel .icon-wrap {
    color: var(--accent);
  }
  .filedrop--panel p {
    margin: 0.35rem 0 0;
  }
  .hint {
    font-size: 0.85rem;
  }

  /* Compact control that sits among the toolbar buttons. */
  .filedrop--inline {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    border-width: 1.5px;
    border-radius: 8px;
    padding: 0.5rem 0.9rem;
    font-weight: 600;
    color: var(--fg);
  }
  .filedrop--inline:hover:not(.disabled),
  .filedrop--inline.dragging {
    color: var(--accent);
  }
  .filedrop--inline.dragging {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .filedrop--inline.disabled {
    opacity: 0.45;
  }
</style>
