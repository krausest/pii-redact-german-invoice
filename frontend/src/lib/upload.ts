import { ACCEPTED_TYPES, MAX_UPLOAD_BYTES } from './types'

export const ACCEPT_ATTR = 'image/png,image/jpeg,application/pdf'

export function validateUpload(file: File): string | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return 'Please choose a PNG, JPEG, or PDF file.'
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `Image is too large (max ${Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024)} MB).`
  }
  return null
}

export function pickUpload(
  file: File | undefined | null,
  opts: { disabled?: boolean; onSelect: (file: File) => void; onError?: (message: string) => void },
): void {
  if (!file || opts.disabled) return
  const err = validateUpload(file)
  if (err) {
    opts.onError?.(err)
    return
  }
  opts.onSelect(file)
}
