import type { Box, Dpi, Note, OutputFormat, Page } from './types'

/**
 * Two endpoints, one job each. `/api/redact` runs the models and reports what it
 * found; `/api/assemble` turns the boxes the user kept into a file. Options are
 * query parameters on both.
 */
const REDACT_URL = '/api/redact'
const ASSEMBLE_URL = '/api/assemble'
const HEALTH_URL = '/health'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function toError(res: Response): Promise<ApiError> {
  let detail = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (body && typeof body.detail === 'string') detail = body.detail
  } catch {
    // non-JSON error body; keep the generic message
  }
  return new ApiError(res.status, detail)
}

/** One page of the JSON report. `image` is the page the boxes refer to — NOT redacted. */
interface ReportPage {
  index: number
  width: number
  height: number
  boxes: Box[]
  notes: Note[]
  image: { content_type: string; data: string }
}

/** The analysis-time options the UI exposes. Each one re-runs detection. */
export interface AnalyzeOptions {
  /** Rasterization DPI for PDF input; image input is used as-is. */
  dpi: Dpi
  /** Flatten a photographed page before OCR. */
  unwarp: boolean
  /** Which model detects PII; `null` until `/health` named the server default. */
  classifier: string | null
}

/** What the server offers: its default classifier and every one a request may name. */
export interface Classifiers {
  default: string
  available: string[]
}

export async function fetchClassifiers(): Promise<Classifiers> {
  const res = await fetch(HEALTH_URL)
  if (!res.ok) throw await toError(res)
  const data = (await res.json()) as { engine: { classifier: string }; classifiers: string[] }
  return { default: data.engine.classifier, available: data.classifiers }
}

/**
 * Upload an image or PDF. The server unwarps each page, detects the PII on it and
 * reports the page (base64 JPEG) with the boxes it suggests, in that page's own
 * pixel space, for the user to review and edit.
 *
 * Both options are named explicitly rather than left to the server's config, so the
 * DPI here and the one `render` sends back cannot drift apart.
 */
export async function analyze(file: File, opts: AnalyzeOptions): Promise<Page[]> {
  const res = await postDocument(file, opts)
  const data = (await res.json()) as { pages: ReportPage[] }
  return data.pages.map((p) => ({
    image: p.image.data,
    width: p.width,
    height: p.height,
    boxes: p.boxes,
    notes: p.notes,
  }))
}

/**
 * The detection trace for `file` under the same options: every OCR line with its
 * pixel box, each classifier match, and the verdict that did or did not produce a
 * box. What diagnoses a wrong box when the document itself cannot be shared.
 *
 * A second request rather than a field on `analyze`: the trace makes the server do
 * work (per-match explanations) that a normal analysis has no use for, and asking
 * for it must not slow down every upload. Detection is deterministic, so the trace
 * describes the boxes already on screen.
 */
export async function fetchDebugLog(file: File, opts: AnalyzeOptions): Promise<string> {
  const res = await postDocument(file, opts, true)
  const data = (await res.json()) as { debug?: string }
  return data.debug ?? ''
}

/** `POST /api/redact` asking for the JSON report; the options are always explicit. */
async function postDocument(file: File, opts: AnalyzeOptions, debug = false): Promise<Response> {
  const params = new URLSearchParams({
    'json-output': 'true',
    'pdf-dpi': String(opts.dpi),
    unwarp: String(opts.unwarp),
  })
  if (opts.classifier) params.set('classifier', opts.classifier)
  if (debug) params.set('debug', 'true')
  const res = await fetch(`${REDACT_URL}?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file,
  })
  if (!res.ok) throw await toError(res)
  return res
}

/**
 * Send the pages back with the boxes the user settled on. This endpoint only fills
 * rectangles — it never re-runs the unwarper — so the boxes stay on the pixels the
 * user actually saw.
 *
 * `dpi` must be the DPI the pages were rasterized at (the one `analyze` was given),
 * or the PDF comes back at the wrong physical page size.
 */
export async function render(
  pages: { image: string; boxes: Box[]; notes: Note[] }[],
  format: OutputFormat,
  dpi: number,
  quality?: number,
): Promise<Blob> {
  const params = new URLSearchParams({ format, dpi: String(dpi) })
  if (quality != null) params.set('jpeg-quality', String(quality))
  const res = await fetch(`${ASSEMBLE_URL}?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      pages: pages.map((p) => ({
        content_type: 'image/jpeg',
        data: p.image,
        boxes: p.boxes,
        notes: p.notes,
      })),
    }),
  })
  if (!res.ok) throw await toError(res)
  return await res.blob()
}
