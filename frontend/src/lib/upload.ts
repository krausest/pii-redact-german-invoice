import { ACCEPTED_TYPES, MAX_UPLOAD_BYTES } from './types'

export const ACCEPT_ATTR = 'image/png,image/jpeg,application/pdf'

/** A reason, not a sentence — the wording lives in the message catalogues. */
export type UploadError = 'unsupported-type' | 'too-large'

export function validateUpload(file: File): UploadError | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return 'unsupported-type'
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return 'too-large'
  }
  return null
}

export function pickUpload(
  file: File | undefined | null,
  opts: { disabled?: boolean; onSelect: (file: File) => void; onError?: (error: UploadError) => void },
): void {
  if (!file || opts.disabled) return
  const err = validateUpload(file)
  if (err) {
    opts.onError?.(err)
    return
  }
  opts.onSelect(file)
}
