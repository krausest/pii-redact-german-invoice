import { analyze, ApiError, type AnalyzeOptions } from './api'
import type { Page } from './types'

export type DocStatus = 'queued' | 'analyzing' | 'ready' | 'error'

/** One uploaded file and everything the editor holds for it — all in this tab only. */
export interface Doc {
  id: number
  /** Path inside the ZIP, or the file name. */
  name: string
  file: File
  kind: 'pdf' | 'image'
  /** Per document: changing a setting re-analyzes this file and no other. */
  options: AnalyzeOptions
  status: DocStatus
  /** The backend's own `detail`, or null for a network failure (the UI words it). */
  error: string | null
  pages: Page[]
  current: number
  /** Sticky: any hand edit since the last analysis, so discarding it asks first. */
  boxesEdited: boolean
  /** Saved (alone or in a ZIP) since the last change. */
  downloaded: boolean
  /** Bumped by every re-analysis, so a result for older options is dropped. */
  generation: number
}

/** Discarding it would lose work: analysis in progress, or a result not downloaded. */
export function isUnsaved(d: Doc): boolean {
  if (d.status === 'queued' || d.status === 'analyzing') return true
  return d.status === 'ready' && !d.downloaded
}

/**
 * The batch. Files are analyzed one at a time, in upload order: a worker runs one
 * redaction at once (`api.max_concurrent_per_worker`), so sending more in parallel
 * would only queue them on the server — against its request timeout.
 */
export class Documents {
  docs = $state<Doc[]>([])
  #queue: number[] = []
  #running = false
  #nextId = 1

  get(id: number | null): Doc | undefined {
    return this.docs.find((d) => d.id === id)
  }

  /** Add files to the end of the queue; returns the new ids. */
  add(files: File[], options: AnalyzeOptions): number[] {
    const ids: number[] = []
    for (const file of files) {
      const id = this.#nextId++
      this.docs.push({
        id,
        name: file.name,
        file,
        kind: file.type === 'application/pdf' ? 'pdf' : 'image',
        options: { ...options },
        status: 'queued',
        error: null,
        pages: [],
        current: 0,
        boxesEdited: false,
        downloaded: false,
        generation: 0,
      })
      this.#queue.push(id)
      ids.push(id)
    }
    this.#pump()
    return ids
  }

  /** Re-run detection with new options, ahead of everything still waiting. */
  reanalyze(id: number, options: AnalyzeOptions) {
    const doc = this.get(id)
    if (!doc) return
    this.#reset(doc, options)
    this.#queue = [id, ...this.#queue.filter((q) => q !== id)]
    this.#pump()
  }

  /** Re-run detection for every document, in upload order, with `update` applied to its options. */
  reanalyzeAll(update: (options: AnalyzeOptions) => AnalyzeOptions) {
    for (const doc of this.docs) this.#reset(doc, update(doc.options))
    this.#queue = this.docs.map((d) => d.id)
    this.#pump()
  }

  #reset(doc: Doc, options: AnalyzeOptions) {
    doc.options = { ...options }
    doc.status = 'queued'
    doc.error = null
    doc.pages = []
    doc.current = 0
    doc.boxesEdited = false
    doc.downloaded = false
    doc.generation++
  }

  remove(id: number) {
    this.docs = this.docs.filter((d) => d.id !== id)
    this.#queue = this.#queue.filter((q) => q !== id)
  }

  clear() {
    this.docs = []
    this.#queue = []
  }

  async #pump() {
    if (this.#running) return
    this.#running = true
    try {
      for (let id = this.#queue.shift(); id !== undefined; id = this.#queue.shift()) {
        const doc = this.get(id)
        if (!doc) continue
        doc.status = 'analyzing'
        const generation = doc.generation
        // Removed, or re-queued with new options, while it ran: drop the result.
        const current = () => {
          const live = this.get(id)
          return live?.generation === generation ? live : undefined
        }
        try {
          const pages = await analyze(doc.file, $state.snapshot(doc.options))
          const live = current()
          if (live) Object.assign(live, { pages, current: 0, status: 'ready' })
        } catch (e) {
          const live = current()
          if (live) Object.assign(live, { status: 'error', error: e instanceof ApiError ? e.message : null })
        }
      }
    } finally {
      this.#running = false
    }
  }
}
