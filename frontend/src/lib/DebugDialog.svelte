<script lang="ts">
  /**
   * Shows the detection trace as plain text, with a way to get it out of the
   * browser. Same modal shape as ConfirmDialog — a native <dialog> driven by an
   * `open` prop — but this one is a reader, not a question: it has a single
   * dismissing action and the body scrolls.
   */
  let {
    open = false,
    title,
    text,
    emptyLabel,
    copyLabel,
    copiedLabel,
    downloadLabel,
    closeLabel,
    filename,
    onclose,
  }: {
    open?: boolean
    title: string
    text: string
    // Required, not defaulted — a default would bake English into the component.
    emptyLabel: string
    copyLabel: string
    copiedLabel: string
    downloadLabel: string
    closeLabel: string
    filename: string
    onclose: () => void
  } = $props()

  let dialogEl: HTMLDialogElement | undefined
  let copied = $state(false)

  $effect(() => {
    if (!dialogEl) return
    if (open && !dialogEl.open) dialogEl.showModal()
    else if (!open && dialogEl.open) dialogEl.close()
  })

  // The confirmation is per opening, so a second visit does not start out
  // claiming the text is already on the clipboard.
  $effect(() => {
    if (open) copied = false
  })

  async function copy() {
    // Not available on an insecure origin, and the user can still select the
    // text or download it — so a failure here is not worth an error banner.
    try {
      await navigator.clipboard.writeText(text)
      copied = true
    } catch {
      copied = false
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }
</script>

<dialog
  bind:this={dialogEl}
  aria-labelledby="debug-title"
  oncancel={(e) => {
    e.preventDefault()
    onclose()
  }}
>
  <h2 id="debug-title">{title}</h2>
  {#if text}
    <pre>{text}</pre>
  {:else}
    <p class="empty">{emptyLabel}</p>
  {/if}
  <div class="actions">
    <button onclick={copy} disabled={!text}>{copied ? copiedLabel : copyLabel}</button>
    <button onclick={download} disabled={!text}>{downloadLabel}</button>
    <button class="primary" onclick={onclose}>{closeLabel}</button>
  </div>
</dialog>

<style>
  dialog {
    /* Wider than ConfirmDialog: a trace line carries a box and a quoted line,
       and wrapping every one of them makes the file unreadable. */
    width: min(62rem, 92vw);
    max-width: none;
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
    margin: 0 0 0.6rem;
    font-size: 1.05rem;
  }
  pre {
    margin: 0 0 1.1rem;
    max-height: min(60vh, 32rem);
    overflow: auto;
    padding: 0.7rem 0.8rem;
    background: color-mix(in srgb, var(--fg) 5%, transparent);
    border: 1px solid var(--border);
    border-radius: 10px;
    font-size: 0.75rem;
    line-height: 1.45;
    /* Long lines scroll rather than wrap; the indentation is what groups a
       classifier's matches under the line they belong to. */
    white-space: pre;
    tab-size: 2;
  }
  .empty {
    margin: 0 0 1.1rem;
    color: var(--muted);
    font-size: 0.9rem;
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
  button:hover:not(:disabled) {
    background: color-mix(in srgb, var(--fg) 6%, transparent);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
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
