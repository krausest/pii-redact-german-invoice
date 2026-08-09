export type Status = 'idle' | 'analyzing' | 'editing'
export type Tool = 'select' | 'draw'
/** What /api/assemble can produce. */
export type OutputFormat = 'pdf' | 'jpeg'

/** [x0, y0, x1, y1] in the page image's own pixel space (matches the API). */
export type Box = [number, number, number, number]

/** A page as the editor holds it: what /api/redact returned, flattened. */
export interface Page {
  /** base64 JPEG of the unwarped page (no data: prefix). */
  image: string
  width: number
  height: number
  boxes: Box[]
}

export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'application/pdf'] as const

/** The rasterization resolutions the UI offers for PDF input. */
export const DPI_CHOICES = [150, 200, 300] as const
export type Dpi = (typeof DPI_CHOICES)[number]

// The SPA sends both options explicitly, so these are the values the user starts
// with — not what the server would have used. Mirror the backend's defaults
// (redaction.pdf_dpi, redaction.unwarp) so the out-of-the-box behaviour matches.
export const DEFAULT_DPI: Dpi = 200
export const DEFAULT_UNWARP = true

// Mirror the backend cap so we can reject before uploading. Keep in sync with the
// redaction service's api.max_upload_bytes.
export const MAX_UPLOAD_BYTES = 30 * 1024 * 1024
