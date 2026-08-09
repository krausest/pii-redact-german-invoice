<script lang="ts">
  import { t } from './i18n.svelte'
  import { DPI_CHOICES, type Dpi } from './types'

  let {
    dpi,
    unwarp,
    onDpiChange,
    onUnwarpChange,
    disabled = false,
    dpiDisabled = false,
    variant = 'panel',
  }: {
    dpi: Dpi
    unwarp: boolean
    onDpiChange: (dpi: Dpi) => void
    onUnwarpChange: (unwarp: boolean) => void
    disabled?: boolean
    /** DPI only affects PDF input, so it is greyed out once an image is loaded. */
    dpiDisabled?: boolean
    /** `panel`: centred row under the idle drop target. `inline`: sits in the toolbar. */
    variant?: 'panel' | 'inline'
  } = $props()

  const m = $derived(t())

  // Deliberately *not* $bindable: the parent may refuse a change (it asks first when
  // boxes were edited), so the control has to be able to snap back. Reading the prop
  // right after the callback gives the new value if the parent took it, the old one
  // if it deferred — either way the element ends up showing the truth.
  function pickDpi(el: HTMLSelectElement) {
    onDpiChange(Number(el.value) as Dpi)
    el.value = String(dpi)
  }

  function pickUnwarp(el: HTMLInputElement) {
    onUnwarpChange(el.checked)
    el.checked = unwarp
  }
</script>

<div class="settings settings--{variant}" role="group" aria-label={m.settings.group}>
  <label class:disabled={disabled || dpiDisabled}>
    <!-- The options read "300 dpi", so the toolbar drops the caption to stay on one line. -->
    {#if variant === 'panel'}<span class="caption">{m.settings.resolution}</span>{/if}
    <select
      value={String(dpi)}
      disabled={disabled || dpiDisabled}
      aria-label={m.settings.resolution}
      title={dpiDisabled ? m.settings.resolutionDisabledTitle : m.settings.resolutionTitle}
      onchange={(e) => pickDpi(e.currentTarget)}
    >
      {#each DPI_CHOICES as choice}
        <option value={String(choice)}>{choice} dpi</option>
      {/each}
    </select>
  </label>

  <label class="check" class:disabled>
    <input
      type="checkbox"
      checked={unwarp}
      {disabled}
      title={m.settings.unwarpTitle}
      onchange={(e) => pickUnwarp(e.currentTarget)}
    />
    <span class="caption">{m.settings.unwarp}</span>
  </label>
</div>

<style>
  .settings {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    font-size: 0.85rem;
    color: var(--muted);
  }
  label {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  label.disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }
  .caption {
    white-space: nowrap;
  }
  select {
    font: inherit;
    color: var(--fg);
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.3rem 0.4rem;
    cursor: pointer;
  }
  select:disabled {
    cursor: not-allowed;
  }
  input[type='checkbox'] {
    width: 15px;
    height: 15px;
    margin: 0;
    accent-color: var(--accent);
    cursor: pointer;
  }
  input[type='checkbox']:disabled {
    cursor: not-allowed;
  }
  select:focus-visible,
  input[type='checkbox']:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* Centred under the idle drop target. */
  .settings--panel {
    justify-content: center;
    margin-top: 0.9rem;
  }

  /* Among the toolbar buttons, on the toolbar's own surface. */
  .settings--inline {
    gap: 0.5rem;
  }
  .settings--inline select {
    background: var(--bg);
  }
</style>
