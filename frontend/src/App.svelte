<script lang="ts">
  import { tick } from 'svelte'
  import ConfirmDialog from './lib/ConfirmDialog.svelte'
  import DebugDialog from './lib/DebugDialog.svelte'
  import DocumentList from './lib/DocumentList.svelte'
  import FileDrop from './lib/FileDrop.svelte'
  import Settings from './lib/Settings.svelte'
  import Toolbar from './lib/Toolbar.svelte'
  import PageEditor from './lib/PageEditor.svelte'
  import LanguageSelect from './lib/LanguageSelect.svelte'
  import { i18n, t } from './lib/i18n.svelte'
  import { fetchClassifiers, fetchDebugLog, render, ApiError } from './lib/api'
  import type { AnalyzeOptions } from './lib/api'
  import { Documents, isUnsaved, type Doc } from './lib/documents.svelte'
  import { zipFiles } from './lib/zip'
  import { DEFAULT_DPI, DEFAULT_UNWARP } from './lib/types'
  import type { Box, OutputFormat, Tool } from './lib/types'
  import { version as appVersion } from '../package.json'

  /** Everything uploaded lives here, in this tab only — the server keeps nothing. */
  const store = new Documents()
  let selectedId = $state<number | null>(null)

  let rendering = $state(false)
  let zipping = $state(false)
  let errorMsg = $state<string | null>(null)
  let tool = $state<Tool>('select')
  /** The selected *box* on the current page. */
  let selected = $state<number | null>(null)

  /** What a new upload starts with: the settings last chosen, on any document. */
  let defaults = $state<AnalyzeOptions>({
    dpi: DEFAULT_DPI,
    unwarp: DEFAULT_UNWARP,
    classifier: null,
  })
  /** Empty until `/health` answers; then the select appears if there is a choice. */
  let classifiers = $state<string[]>([])

  /** An action waiting on the confirm dialog. */
  type Confirm =
    | { kind: 'settings'; id: number; options: AnalyzeOptions }
    | { kind: 'remove'; id: number }
    | { kind: 'discard-all' }
    | { kind: 'replace'; files: File[] }
  let confirm = $state<Confirm | null>(null)

  /** The detection trace, once fetched; null while the dialog is closed. */
  let debugLog = $state<string | null>(null)
  let debugBusy = $state(false)

  const m = $derived(t())
  const doc = $derived(store.get(selectedId))
  const page = $derived(doc?.pages[doc.current] ?? null)
  const batch = $derived(store.docs.length > 1)
  /** The page on screen can be edited; anything else greys the editing controls. */
  const busy = $derived(doc?.status !== 'ready' || rendering || zipping)
  const allSettled = $derived(store.docs.every((d) => d.status === 'ready' || d.status === 'error'))
  const anyReady = $derived(store.docs.some((d) => d.status === 'ready'))
  /** What discarding the batch would lose — the one rule behind every confirmation. */
  const unsaved = $derived(store.docs.filter(isUnsaved))

  // The server's default, not a hard-coded one: the config picks it. A failed
  // lookup only hides the select — requests then omit the parameter.
  fetchClassifiers()
    .then((c) => {
      classifiers = c.available
      defaults.classifier ??= c.default
    })
    .catch(() => {})

  // index.html carries `lang="en"` and the English title for the pre-mount moment;
  // once we know the locale, the document shell follows it.
  $effect(() => {
    document.documentElement.lang = i18n.locale
    document.title = m.app.documentTitle
  })

  // Nothing survives a reload — warn while anything would be lost.
  $effect(() => {
    if (!unsaved.length) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  })

  /** `folder/scan.pdf` → `folder/`, `scan`, so outputs keep the ZIP's layout. */
  function splitName(name: string): { dir: string; stem: string } {
    const slash = name.lastIndexOf('/') + 1
    return { dir: name.slice(0, slash), stem: name.slice(slash).replace(/\.[^.]+$/, '') || 'document' }
  }

  function outputName(d: Doc): string {
    const { dir, stem } = splitName(d.name)
    return `${dir}redacted-${stem}.${d.kind === 'pdf' ? 'pdf' : 'jpg'}`
  }

  function selectDoc(id: number | null) {
    selectedId = id
    selected = null
    tool = 'select'
  }

  function onSelectFiles(files: File[]) {
    errorMsg = null
    const wasIdle = !store.docs.length
    const ids = store.add(files, $state.snapshot(defaults))
    if (!doc) selectDoc(ids[0])
    if (wasIdle) focusToolbar()
  }

  /** "New upload" starts over — asking first if that loses anything. */
  function requestReplace(files: File[]) {
    if (unsaved.length) confirm = { kind: 'replace', files }
    else replaceAll(files)
  }

  function replaceAll(files: File[]) {
    store.clear()
    selectDoc(null)
    debugLog = null
    onSelectFiles(files)
  }

  function requestDiscardAll() {
    if (unsaved.length) confirm = { kind: 'discard-all' }
    else reset()
  }

  function sameOptions(a: AnalyzeOptions, b: AnalyzeOptions) {
    return a.dpi === b.dpi && a.unwarp === b.unwarp && a.classifier === b.classifier
  }

  /**
   * A settings change applies to the document on screen only, and re-analyzes it —
   * after asking, if that would throw away hand-drawn or hand-deleted boxes.
   */
  function requestSettings(next: AnalyzeOptions) {
    if (!doc) {
      defaults = next
      return
    }
    if (sameOptions(next, doc.options)) return
    if (doc.boxesEdited) {
      confirm = { kind: 'settings', id: doc.id, options: next }
      return
    }
    applySettings(doc.id, next)
  }

  function applySettings(id: number, next: AnalyzeOptions) {
    defaults = next
    store.reanalyze(id, next)
  }

  function requestRemove(id: number) {
    const d = store.get(id)
    if (d && isUnsaved(d)) confirm = { kind: 'remove', id }
    else removeDoc(id)
  }

  function removeDoc(id: number) {
    const index = store.docs.findIndex((d) => d.id === id)
    store.remove(id)
    if (!store.docs.length) {
      reset()
      return
    }
    if (selectedId === id) selectDoc(store.docs[Math.min(index, store.docs.length - 1)].id)
  }

  function confirmPending() {
    const next = confirm
    confirm = null
    if (next?.kind === 'settings') applySettings(next.id, next.options)
    else if (next?.kind === 'remove') removeDoc(next.id)
    else if (next?.kind === 'discard-all') reset()
    else if (next?.kind === 'replace') replaceAll(next.files)
  }

  const confirmText = $derived.by(() => {
    if (confirm?.kind === 'remove') {
      const name = store.get(confirm.id)?.name ?? ''
      return { title: m.dialog.removeTitle, message: m.dialog.removeMessage(name), label: m.dialog.removeConfirm }
    }
    if (confirm?.kind === 'replace') {
      return {
        title: m.dialog.replaceTitle,
        message: m.dialog.replaceMessage(unsaved.length),
        label: m.dialog.replaceConfirm,
      }
    }
    if (confirm?.kind === 'discard-all') {
      return { title: m.dialog.discardAllTitle, message: m.dialog.discardAllMessage, label: m.dialog.discardAllConfirm }
    }
    return { title: m.dialog.discardTitle, message: m.dialog.discardMessage, label: m.dialog.reanalyze }
  })

  async function focusToolbar() {
    await tick()
    document.querySelector<HTMLElement>('.toolbar button')?.focus()
  }

  async function focusFileDrop() {
    await tick()
    document.querySelector<HTMLElement>('.filedrop--panel')?.focus()
  }

  function addBox(box: Box) {
    if (!doc || !page) return
    page.boxes.push(box)
    selected = page.boxes.length - 1
    tool = 'select'
    doc.boxesEdited = true
    doc.downloaded = false
  }

  function deleteSelected() {
    if (!doc || !page || selected == null) return
    page.boxes.splice(selected, 1)
    selected = null
    doc.boxesEdited = true
    doc.downloaded = false
  }

  function goto(index: number) {
    if (!doc) return
    doc.current = index
    selected = null
  }

  /** The redacted file for one document — assembled by the server from the kept boxes. */
  function renderDoc(d: Doc): Promise<Blob> {
    const format: OutputFormat = d.kind === 'pdf' ? 'pdf' : 'jpeg'
    return render(
      d.pages.map((p) => ({ image: p.image, boxes: p.boxes })),
      format,
      d.options.dpi,
    )
  }

  function save(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  async function download() {
    if (!doc) return
    rendering = true
    errorMsg = null
    try {
      const target = doc
      save(await renderDoc(target), outputName(target).split('/').pop()!)
      target.downloaded = true
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : m.errors.renderFailed
    } finally {
      rendering = false
    }
  }

  /**
   * Every finished document in one ZIP. Assembled one at a time, like the analysis:
   * each request stays small, and the server never holds the batch.
   */
  async function downloadAll() {
    zipping = true
    errorMsg = null
    const entries: { path: string; blob: Blob }[] = []
    const included: Doc[] = []
    const used = new Set<string>()
    let skipped = 0
    try {
      for (const d of store.docs) {
        if (d.status !== 'ready') {
          skipped++
          continue
        }
        try {
          const blob = await renderDoc(d)
          // The same name twice (two uploads of one file) must not overwrite.
          let path = outputName(d)
          for (let n = 2; used.has(path); n++) path = outputName(d).replace(/(\.[^.]+)$/, ` (${n})$1`)
          used.add(path)
          entries.push({ path, blob })
          included.push(d)
        } catch {
          skipped++
        }
      }
      if (entries.length) {
        save(await zipFiles(entries), 'redacted-documents.zip')
        for (const d of included) d.downloaded = true
      }
      if (skipped) errorMsg = m.errors.zipSkipped(skipped)
    } catch {
      errorMsg = m.errors.zipFailed
    } finally {
      zipping = false
    }
  }

  /**
   * Fetch the detection trace for the document on screen. Deliberately its own
   * request: it changes nothing here — not the pages, not the boxes, not
   * `boxesEdited` — so it needs no discard confirmation, and the extra analysis
   * is paid only by whoever asks for it.
   */
  async function showDebugLog() {
    if (!doc) return
    debugBusy = true
    errorMsg = null
    try {
      debugLog = await fetchDebugLog(doc.file, $state.snapshot(doc.options))
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : m.errors.debugFailed
    } finally {
      debugBusy = false
    }
  }

  function reset() {
    store.clear()
    selectDoc(null)
    errorMsg = null
    confirm = null
    debugLog = null
    focusFileDrop()
  }

  function onKey(e: KeyboardEvent) {
    // A modal dialog handles its own Escape; the keydown still reaches the window,
    // and would otherwise reset the document sitting behind it.
    if (confirm || debugLog !== null) return
    if (!store.docs.length) return
    // Same rule as "Discard all": asks first if anything would be lost.
    if (e.key === 'Escape' && !rendering && !zipping) {
      e.preventDefault()
      requestDiscardAll()
      return
    }
    if (busy) return
    if ((e.key === 'Delete' || e.key === 'Backspace') && selected != null) {
      e.preventDefault()
      deleteSelected()
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<main class:wide={batch}>
  <header>
    <h1>{m.app.title}</h1>
    <p class="sub">{m.app.subtitle}</p>
  </header>

  {#if !doc}
    <FileDrop onselect={onSelectFiles} onerror={(msg) => (errorMsg = msg)} />
    <Settings options={defaults} {classifiers} onChange={requestSettings} />
  {:else}
    <div class="workspace" class:batch>
      {#if batch}
        <DocumentList
          docs={store.docs}
          {selectedId}
          onselect={selectDoc}
          onremove={requestRemove}
          ondiscardAll={requestDiscardAll}
          disabled={zipping}
        />
      {/if}
      <div class="editor">
        <Toolbar
          bind:tool
          current={doc.current}
          total={doc.pages.length}
          ongoto={goto}
          canDelete={selected != null}
          {busy}
          {rendering}
          downloadLabel={doc.kind === 'pdf' ? m.toolbar.downloadPdf : m.toolbar.downloadImage}
          options={doc.options}
          {classifiers}
          dpiDisabled={doc.kind === 'image'}
          onOptionsChange={requestSettings}
          onDelete={deleteSelected}
          onDownload={download}
          onDownloadAll={batch ? downloadAll : undefined}
          downloadAllReady={allSettled && anyReady}
          {zipping}
          uploadDisabled={rendering || zipping}
          onSelectFiles={requestReplace}
          onFileError={(msg) => (errorMsg = msg)}
        />
        {#if doc.status === 'queued' || doc.status === 'analyzing'}
          <div class="skeleton" aria-busy="true" aria-live="polite">
            {#if doc.status === 'analyzing'}
              <span class="spinner" aria-hidden="true"></span>
              {m.app.analyzing}
            {:else}
              {m.docs.waiting}
            {/if}
          </div>
        {:else if doc.status === 'error'}
          <div class="skeleton failed" role="alert">
            <span>{doc.error ?? m.errors.analyzeFailed}</span>
            <button class="retry" onclick={() => store.reanalyze(doc.id, doc.options)}>{m.docs.retry}</button>
          </div>
        {:else if page}
          <p class="hint">
            {tool === 'draw' ? m.app.hintDraw : m.app.hintSelect}
            · {m.app.boxCount(page.boxes.length)}
          </p>
          <div class="stage">
            <PageEditor {page} {tool} bind:selected onadd={addBox} />
          </div>
        {/if}
      </div>
    </div>
  {/if}
  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}

  <ConfirmDialog
    open={confirm != null}
    title={confirmText.title}
    message={confirmText.message}
    confirmLabel={confirmText.label}
    cancelLabel={m.dialog.cancel}
    onconfirm={confirmPending}
    oncancel={() => (confirm = null)}
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
    filename={`debug-${doc ? splitName(doc.name).stem : 'document'}.txt`}
    onclose={() => (debugLog = null)}
  />
</main>

<footer class:wide={batch}>
  <p>
    © Stefan Krause ·
    <a href="https://github.com/krausest/pii-redact-german-invoice" target="_blank" rel="noopener noreferrer">GitHub</a>
    · v{appVersion} ·
    <button
      class="link"
      onclick={showDebugLog}
      disabled={!doc || busy || debugBusy}
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
  /* A batch adds the document list beside the editor; the extra width keeps the
     toolbar, ZIP button included, on one line wherever the window allows it. */
  main.wide,
  footer.wide {
    max-width: 1400px;
  }
  .workspace.batch {
    display: grid;
    grid-template-columns: 230px minmax(0, 1fr);
    gap: 0.9rem;
    align-items: start;
  }
  @media (max-width: 760px) {
    .workspace.batch {
      grid-template-columns: 1fr;
    }
  }
  .failed {
    flex-direction: column;
    color: var(--error);
    animation: none;
  }
  .retry {
    font: inherit;
    font-weight: 600;
    padding: 0.4rem 0.9rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--fg);
    cursor: pointer;
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
