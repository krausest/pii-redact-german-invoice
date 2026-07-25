<script lang="ts">
  import { tick } from 'svelte'
  import FileDrop from './lib/FileDrop.svelte'
  import Toolbar from './lib/Toolbar.svelte'
  import PageEditor from './lib/PageEditor.svelte'
  import { analyze, render, ApiError } from './lib/api'
  import type { Box, OutputFormat, Page, Status, Tool } from './lib/types'

  let status = $state<Status>('idle')
  let rendering = $state(false)
  let errorMsg = $state<string | null>(null)

  let pages = $state<Page[]>([])
  let current = $state(0)
  let tool = $state<Tool>('select')
  let selected = $state<number | null>(null)

  let baseName = $state('document')
  let inputKind = $state<'image' | 'pdf'>('image')

  const busy = $derived(status === 'analyzing' || rendering)
  const page = $derived(pages[current] ?? null)

  async function onSelectFile(file: File) {
    errorMsg = null
    baseName = file.name.replace(/\.[^.]+$/, '') || 'document'
    inputKind = file.type === 'application/pdf' ? 'pdf' : 'image'
    const wasIdle = status === 'idle'
    pages = []
    current = 0
    selected = null
    tool = 'select'
    status = 'analyzing'
    if (wasIdle) focusToolbar()
    try {
      pages = await analyze(file)
      status = 'editing'
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : 'Could not analyze the file. Is the service running?'
      status = 'idle'
      focusFileDrop()
    }
  }

  async function focusToolbar() {
    await tick()
    document.querySelector<HTMLElement>('.toolbar button')?.focus()
  }

  async function focusFileDrop() {
    await tick()
    document.querySelector<HTMLElement>('.filedrop--panel')?.focus()
  }

  function addBox(box: Box) {
    pages[current].boxes.push(box)
    selected = pages[current].boxes.length - 1
    tool = 'select'
  }

  function deleteSelected() {
    if (selected == null) return
    pages[current].boxes.splice(selected, 1)
    selected = null
  }

  function goto(index: number) {
    current = index
    selected = null
  }

  async function download() {
    rendering = true
    errorMsg = null
    const format: OutputFormat = inputKind === 'pdf' ? 'pdf' : 'jpeg'
    try {
      const blob = await render(
        pages.map((p) => ({ image: p.image, boxes: p.boxes })),
        format,
      )
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `redacted-${baseName}.${format === 'pdf' ? 'pdf' : 'jpg'}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : 'Could not render the output.'
    } finally {
      rendering = false
    }
  }

  function reset() {
    pages = []
    current = 0
    selected = null
    tool = 'select'
    errorMsg = null
    status = 'idle'
    focusFileDrop()
  }

  function onKey(e: KeyboardEvent) {
    if (status === 'idle') return
    if (e.key === 'Escape' && !busy) {
      e.preventDefault()
      reset()
      return
    }
    if (status !== 'editing') return
    if ((e.key === 'Delete' || e.key === 'Backspace') && selected != null) {
      e.preventDefault()
      deleteSelected()
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<main>
  <header>
    <h1>PII Redaction</h1>
    <p class="sub">
      Upload an invoice image or PDF. Review the suggested redactions, add or remove
      boxes, then download the redacted result.
    </p>
  </header>

  {#if status === 'idle'}
    <FileDrop onselect={onSelectFile} onerror={(m) => (errorMsg = m)} disabled={busy} />
  {:else}
    <Toolbar
      bind:tool
      current={current}
      total={pages.length}
      ongoto={goto}
      canDelete={selected != null}
      {busy}
      {rendering}
      downloadLabel={inputKind === 'pdf' ? 'Download redacted PDF' : 'Download redacted image'}
      onDelete={deleteSelected}
      onDownload={download}
      onSelectFile={onSelectFile}
      onFileError={(m) => (errorMsg = m)}
    />
    {#if status === 'analyzing'}
      <div class="skeleton" aria-busy="true" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span> Analyzing — unwarping pages and detecting PII…
      </div>
    {:else if status === 'editing' && page}
      <p class="hint">
        {tool === 'draw' ? 'Drag on the page to draw a redaction box.' : 'Click a box to select it, then Delete to remove it.'}
        · {page.boxes.length} box{page.boxes.length === 1 ? '' : 'es'} on this page
      </p>
      <div class="stage">
        <PageEditor {page} {tool} bind:selected onadd={addBox} />
      </div>
    {/if}
  {/if}
  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}
</main>

<style>
  main {
    width: 100%;
    max-width: 820px;
  }
  header {
    text-align: center;
    margin-bottom: 1.5rem;
  }
  h1 {
    margin: 0 0 0.25rem;
    font-size: 1.6rem;
  }
  .sub {
    margin: 0 auto;
    max-width: 34rem;
    color: var(--muted);
    font-size: 0.95rem;
  }
  .hint {
    margin: 0 0 0.6rem;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .stage {
    text-align: center;
  }
  .skeleton {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    justify-content: center;
    color: var(--muted);
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 3rem 1.5rem;
    animation: shimmer 1.6s ease-in-out infinite;
  }
  @keyframes shimmer {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.6;
    }
  }
  .error {
    margin-top: 1rem;
    color: var(--error);
    font-weight: 500;
  }
  .spinner {
    width: 15px;
    height: 15px;
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
