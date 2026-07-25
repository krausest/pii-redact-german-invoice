import type { Box, OutputFormat, Page } from './types'

/**
 * Two endpoints, one job each. `/api/redact` runs the models and reports what it
 * found; `/api/assemble` turns the boxes the user kept into a file. Options are
 * query parameters on both.
 */
const REDACT_URL = '/api/redact'
const ASSEMBLE_URL = '/api/assemble'

/** The DPI the backend rasterizes PDFs at, so assembled pages get their true size. */
const PAGE_DPI = 200

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
  image: { content_type: string; data: string }
}

/**
 * Upload an image or PDF. The server unwarps each page, detects the PII on it and
 * reports the page (base64 JPEG) with the boxes it suggests, in that page's own
 * pixel space, for the user to review and edit.
 */
export async function analyze(file: File): Promise<Page[]> {
  const res = await fetch(`${REDACT_URL}?json-output=true`, {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file,
  })
  if (!res.ok) throw await toError(res)
  const data = (await res.json()) as { pages: ReportPage[] }
  return data.pages.map((p) => ({
    image: p.image.data,
    width: p.width,
    height: p.height,
    boxes: p.boxes,
  }))
}

/**
 * Send the pages back with the boxes the user settled on. This endpoint only fills
 * rectangles — it never re-runs the unwarper — so the boxes stay on the pixels the
 * user actually saw.
 */
export async function render(
  pages: { image: string; boxes: Box[] }[],
  format: OutputFormat,
  quality?: number,
): Promise<Blob> {
  const params = new URLSearchParams({ format, dpi: String(PAGE_DPI) })
  if (quality != null) params.set('jpeg-quality', String(quality))
  const res = await fetch(`${ASSEMBLE_URL}?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      pages: pages.map((p) => ({ content_type: 'image/jpeg', data: p.image, boxes: p.boxes })),
    }),
  })
  if (!res.ok) throw await toError(res)
  return await res.blob()
}
