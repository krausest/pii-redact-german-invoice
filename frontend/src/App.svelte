<script lang="ts">
  import { tick } from 'svelte'
  import ConfirmDialog from './lib/ConfirmDialog.svelte'
  import DebugDialog from './lib/DebugDialog.svelte'
  import FileDrop from './lib/FileDrop.svelte'
  import Settings from './lib/Settings.svelte'
  import Toolbar from './lib/Toolbar.svelte'
  import PageEditor from './lib/PageEditor.svelte'
  import LanguageSelect from './lib/LanguageSelect.svelte'
  import { i18n, t } from './lib/i18n.svelte'
  import { analyze, fetchDebugLog, render, ApiError } from './lib/api'
  import { DEFAULT_DPI, DEFAULT_UNWARP } from './lib/types'
  import type { Box, Dpi, OutputFormat, Page, Status, Tool } from './lib/types'
  import { version as appVersion } from '../package.json'

  let status = $state<Status>('idle')
  let rendering = $state(false)
  let errorMsg = $state<string | null>(null)

  let pages = $state<Page[]>([])
  let current = $state(0)
  let tool = $state<Tool>('select')
  let selected = $state<number | null>(null)

  let baseName = $state('document')
  let inputKind = $state<'image' | 'pdf'>('image')
  /** Kept so a settings change can re-analyze without asking for the file again. */
  let file = $state<File | null>(null)

  let dpi = $state<Dpi>(DEFAULT_DPI)
  let unwarp = $state(DEFAULT_UNWARP)
  /** Sticky: any hand edit since the last analyze, so we can warn before discarding them. */
  let boxesEdited = $state(false)
  /** A settings change waiting on the confirm dialog. */
  let pending = $state<{ dpi: Dpi; unwarp: boolean } | null>(null)

  /** The detection trace, once fetched; null while the dialog is closed. */
  let debugLog = $state<string | null>(null)
  let debugBusy = $state(false)

  const busy = $derived(status === 'analyzing' || rendering)
  const page = $derived(pages[current] ?? null)
  const m = $derived(t())

  // index.html carries `lang="en"` and the English title for the pre-mount moment;
  // once we know the locale, the document shell follows it.
  $effect(() => {
    document.documentElement.lang = i18n.locale
    document.title = m.app.documentTitle
  })

  async function onSelectFile(next: File) {
    baseName = next.name.replace(/\.[^.]+$/, '') || 'document'
    inputKind = next.type === 'application/pdf' ? 'pdf' : 'image'
    file = next
    if (status === 'idle') focusToolbar()
    await runAnalyze(next)
  }

  async function runAnalyze(source: File) {
    errorMsg = null
    pages = []
    current = 0
    selected = null
    tool = 'select'
    boxesEdited = false
    status = 'analyzing'
    try {
      pages = await analyze(source, { dpi, unwarp })
      status = 'editing'
    } catch (e) {
      // An ApiError carries the backend's own English `detail`, shown as-is.
      errorMsg = e instanceof ApiError ? e.message : m.errors.analyzeFailed
      file = null
      status = 'idle'
      focusFileDrop()
    }
  }

  /**
   * A settings change is only meaningful if detection runs again, so it re-analyzes —
   * after asking, if that would throw away hand-drawn or hand-deleted boxes.
   */
  function requestSettings(next: { dpi: Dpi; unwarp: boolean }) {
    if (next.dpi === dpi && next.unwarp === unwarp) return
    if (status !== 'editing' || !file) {
      dpi = next.dpi
      unwarp = next.unwarp
      return
    }
    if (boxesEdited) {
      pending = next
      return
    }
    applySettings(next)
  }

  function applySettings(next: { dpi: Dpi; unwarp: boolean }) {
    dpi = next.dpi
    unwarp = next.unwarp
    if (file) runAnalyze(file)
  }

  function confirmPending() {
    const next = pending
    pending = null
    if (next) applySettings(next)
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
    boxesEdited = true
  }

  function deleteSelected() {
    if (selected == null) return
    pages[current].boxes.splice(selected, 1)
    selected = null
    boxesEdited = true
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
        dpi,
      )
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `redacted-${baseName}.${format === 'pdf' ? 'pdf' : 'jpg'}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : m.errors.renderFailed
    } finally {
      rendering = false
    }
  }

  /**
   * Fetch the detection trace for the document on screen. Deliberately its own
   * request: it changes nothing here — not the pages, not the boxes, not
   * `boxesEdited` — so it needs no discard confirmation, and the extra analysis
   * is paid only by whoever asks for it.
   */
  async function showDebugLog() {
    if (!file) return
    debugBusy = true
    errorMsg = null
    try {
      debugLog = await fetchDebugLog(file, { dpi, unwarp })
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : m.errors.debugFailed
    } finally {
      debugBusy = false
    }
  }

  function reset() {
    pages = []
    current = 0
    selected = null
    tool = 'select'
    errorMsg = null
    file = null
    boxesEdited = false
    pending = null
    debugLog = null
    status = 'idle'
    focusFileDrop()
  }

  function onKey(e: KeyboardEvent) {
    // A modal dialog handles its own Escape; the keydown still reaches the window,
    // and would otherwise reset the document sitting behind it.
    if (pending || debugLog !== null) return
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
    <h1>{m.app.title}</h1>
    <p class="sub">{m.app.subtitle}</p>
  </header>

  {#if status === 'idle'}
    <FileDrop onselect={onSelectFile} onerror={(m) => (errorMsg = m)} disabled={busy} />
    <Settings
      {dpi}
      {unwarp}
      onDpiChange={(d) => requestSettings({ dpi: d, unwarp })}
      onUnwarpChange={(u) => requestSettings({ dpi, unwarp: u })}
      disabled={busy}
    />
  {:else}
    <Toolbar
      bind:tool
      current={current}
      total={pages.length}
      ongoto={goto}
      canDelete={selected != null}
      {busy}
      {rendering}
      downloadLabel={inputKind === 'pdf' ? m.toolbar.downloadPdf : m.toolbar.downloadImage}
      {dpi}
      {unwarp}
      dpiDisabled={inputKind === 'image'}
      onDpiChange={(d) => requestSettings({ dpi: d, unwarp })}
      onUnwarpChange={(u) => requestSettings({ dpi, unwarp: u })}
      onDelete={deleteSelected}
      onDownload={download}
      onSelectFile={onSelectFile}
      onFileError={(m) => (errorMsg = m)}
    />
    {#if status === 'analyzing'}
      <div class="skeleton" aria-busy="true" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span>
        {m.app.analyzing}
      </div>
    {:else if status === 'editing' && page}
      <p class="hint">
        {tool === 'draw' ? m.app.hintDraw : m.app.hintSelect}
        · {m.app.boxCount(page.boxes.length)}
      </p>
      <div class="stage">
        <PageEditor {page} {tool} bind:selected onadd={addBox} />
      </div>
    {/if}
  {/if}
  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}

  <ConfirmDialog
    open={pending != null}
    title={m.dialog.discardTitle}
    message={m.dialog.discardMessage}
    confirmLabel={m.dialog.reanalyze}
    cancelLabel={m.dialog.cancel}
    onconfirm={confirmPending}
    oncancel={() => (pending = null)}
  />

  <DebugDialog
    open={debugLog !== null}
    title={m.debug.title}
    text={debugLog ?? ''}
    emptyLabel={m.debug.empty}
    copyLabel={m.debug.copy}
    copiedLabel={m.debug.copied}
    downloadLabel={m.debug.download}
    closeLabel={m.debug.close}
    filename={`debug-${baseName}.txt`}
    onclose={() => (debugLog = null)}
  />
</main>

<footer>
  <p>
    © Stefan Krause ·
    <a href="https://github.com/krausest/pii-redact-german-invoice" target="_blank" rel="noopener noreferrer">GitHub</a>
    · v{appVersion} ·
    <button
      class="link"
      onclick={showDebugLog}
      disabled={!file || status !== 'editing' || busy || debugBusy}
      title={m.debug.buttonTitle}
    >
      {debugBusy ? m.debug.fetching : m.debug.button}
    </button>
    ·
    <LanguageSelect />
  </p>
</footer>

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
  footer {
    width: 100%;
    max-width: 820px;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    text-align: center;
  }
  footer p {
    margin: 0;
    color: #ccc;
    font-size: 0.8rem;
  }
  footer a {
    color: var(--accent);
    text-decoration: none;
  }
  footer a:hover {
    text-decoration: underline;
  }
  /* A footer control, so it reads as one of the links beside it rather than as
     an action on the document — which is what it is: a diagnostic, not a step. */
  footer button.link {
    font: inherit;
    padding: 0;
    border: none;
    background: none;
    color: var(--accent);
    cursor: pointer;
  }
  footer button.link:hover:not(:disabled) {
    text-decoration: underline;
  }
  footer button.link:disabled {
    color: inherit;
    opacity: 0.55;
    cursor: default;
  }
  footer button.link:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 4px;
  }
</style>
