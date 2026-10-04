import { ACCEPTED_TYPES, MAX_UPLOAD_BYTES } from './types'

export const ACCEPT_ATTR = 'image/png,image/jpeg,application/pdf,application/zip,.zip'

/** A reason, not a sentence — the wording lives in the message catalogues. */
export type UploadError = 'unsupported-type' | 'too-large' | 'bad-zip' | 'empty-zip'

export function validateUpload(file: File): UploadError | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return 'unsupported-type'
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return 'too-large'
  }
  return null
}
