import { unzipSync, zipSync, type Unzipped } from 'fflate'
import { MAX_UPLOAD_BYTES } from './types'
import { validateUpload, type UploadError } from './upload'

/** One file that blocked an upload, and why. The wording lives in the catalogues. */
export interface Rejection {
  name: string
  error: UploadError
}

export type Expanded = { files: File[] } | { rejected: Rejection[] }

// ZIP entries carry no MIME type, so the extension stands in for it.
const TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
}

export function isZip(file: File): boolean {
  return (
    file.type === 'application/zip' ||
    file.type === 'application/x-zip-compressed' ||
    file.name.toLowerCase().endsWith('.zip')
  )
}

/** OS clutter a ZIP picks up on the way (Finder, Explorer) — never the user's content. */
function isClutter(path: string): boolean {
  return path.endsWith('/') || path.split('/').some((part) => part.startsWith('.') || part === '__MACOSX')
}

/**
 * The documents a drop or file selection holds, ZIPs unpacked. All or nothing: one
 * file that cannot be processed rejects the whole upload, so a batch never silently
 * comes back short.
 */
export async function expandUpload(input: File[]): Promise<Expanded> {
  const files: File[] = []
  const rejected: Rejection[] = []
  for (const file of input) {
    if (!isZip(file)) {
      const error = validateUpload(file)
      if (error) rejected.push({ name: file.name, error })
      else files.push(file)
      continue
    }
    const unpacked = await unpack(file, rejected)
    files.push(...unpacked)
  }
  return rejected.length ? { rejected } : { files }
}

async function unpack(zip: File, rejected: Rejection[]): Promise<File[]> {
  let entries: Unzipped
  const before = rejected.length
  try {
    entries = unzipSync(new Uint8Array(await zip.arrayBuffer()), {
      // Decided from the directory, *before* inflating: an oversized entry is
      // never expanded, which is also the zip-bomb guard.
      filter: (info) => {
        if (isClutter(info.name)) return false
        const ext = info.name.split('.').pop()?.toLowerCase() ?? ''
        if (!(ext in TYPE_BY_EXTENSION)) {
          rejected.push({ name: `${zip.name}: ${info.name}`, error: 'unsupported-type' })
          return false
        }
        if (info.originalSize > MAX_UPLOAD_BYTES) {
          rejected.push({ name: `${zip.name}: ${info.name}`, error: 'too-large' })
          return false
        }
        return true
      },
    })
  } catch {
    rejected.push({ name: zip.name, error: 'bad-zip' })
    return []
  }
  const files = Object.entries(entries).map(([path, data]) => {
    const ext = path.split('.').pop()!.toLowerCase()
    return new File([data as Uint8Array<ArrayBuffer>], path, { type: TYPE_BY_EXTENSION[ext] })
  })
  if (!files.length && rejected.length === before) rejected.push({ name: zip.name, error: 'empty-zip' })
  return files
}

/** One ZIP of finished files. Stored, not deflated: JPEG and PDF are compressed already. */
export async function zipFiles(entries: { path: string; blob: Blob }[]): Promise<Blob> {
  const data: Record<string, Uint8Array> = {}
  for (const { path, blob } of entries) data[path] = new Uint8Array(await blob.arrayBuffer())
  return new Blob([zipSync(data, { level: 0 }) as Uint8Array<ArrayBuffer>], { type: 'application/zip' })
}
