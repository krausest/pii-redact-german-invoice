<script lang="ts">
  let {
    open = false,
    title,
    message,
    confirmLabel,
    cancelLabel,
    onconfirm,
    oncancel,
  }: {
    open?: boolean
    title: string
    message: string
    // Required, not defaulted — a default label would bake English into the component.
    confirmLabel: string
    cancelLabel: string
    onconfirm: () => void
    oncancel: () => void
  } = $props()

  let dialogEl: HTMLDialogElement | undefined

  // showModal() traps focus and gives us Escape + the backdrop for free; both arrive
  // as the element's own `cancel` event, which we forward to the caller.
  $effect(() => {
    if (!dialogEl) return
    if (open && !dialogEl.open) dialogEl.showModal()
    else if (!open && dialogEl.open) dialogEl.close()
  })
</script>

<dialog
  bind:this={dialogEl}
  aria-labelledby="confirm-title"
  oncancel={(e) => {
    e.preventDefault()
    oncancel()
  }}
>
  <h2 id="confirm-title">{title}</h2>
  <p>{message}</p>
  <div class="actions">
    <button onclick={oncancel}>{cancelLabel}</button>
    <button class="primary" onclick={onconfirm}>{confirmLabel}</button>
  </div>
</dialog>

<style>
  dialog {
    max-width: 26rem;
    margin: auto;
    padding: 1.25rem 1.4rem;
    color: var(--fg);
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
  }
  dialog::backdrop {
    background: rgb(0 0 0 / 0.45);
  }
  h2 {
    margin: 0 0 0.5rem;
    font-size: 1.05rem;
  }
  p {
    margin: 0 0 1.1rem;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.45;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
  button {
    font: inherit;
    font-weight: 600;
    padding: 0.45rem 0.9rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--fg);
    cursor: pointer;
  }
  button:hover {
    background: color-mix(in srgb, var(--fg) 6%, transparent);
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  button.primary {
    background: var(--accent);
    color: var(--accent-fg);
    border-color: transparent;
  }
  button.primary:hover {
    filter: brightness(1.08);
  }
</style>
