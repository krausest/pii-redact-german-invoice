/**
 * The source of truth for every user-facing string in the SPA. `de.ts` is typed
 * against this shape, so a key added here without a German counterpart is a
 * `svelte-check` error rather than an `undefined` on screen.
 *
 * Interpolated values are functions — which is also where plurals live, each
 * locale writing its own rule. Two locales do not need ICU machinery.
 */
import { MAX_UPLOAD_BYTES } from '../types'

const MAX_UPLOAD_MB = Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024)

export const en = {
  app: {
    documentTitle: 'PII Redaction',
    title: 'PII Redaction',
    subtitle:
      'Upload an invoice image or PDF. Review the suggested redactions, add or remove boxes, then download the redacted result.',
    analyzing: 'Analyzing — unwarping pages and detecting PII…',
    hintDraw: 'Drag on the page to draw a redaction box.',
    hintSelect: 'Click a box to select it, then Delete to remove it.',
    boxCount: (n: number) => `${n} box${n === 1 ? '' : 'es'} on this page`,
  },
  toolbar: {
    tools: 'Tools',
    select: 'Select',
    drawBox: 'Draw box',
    delete: 'Delete',
    deleteSelected: 'Delete selected box',
    pageNavigation: 'Page navigation',
    previousPage: 'Previous page',
    nextPage: 'Next page',
    download: 'Download',
    rendering: 'Rendering…',
    downloadPdf: 'Download redacted PDF',
    downloadImage: 'Download redacted image',
  },
  filedrop: {
    // Split so each locale can put the emphasis where its grammar wants it.
    dropBefore: '',
    dropStrong: 'Drag & drop',
    dropAfter: ' a PNG, JPEG, or PDF here',
    orClick: 'or click to choose a file',
    newUpload: 'New upload',
    inlineTitle: 'Upload a new file — click or drop',
  },
  settings: {
    group: 'Analysis settings',
    resolution: 'Resolution',
    resolutionTitle: 'Resolution PDF pages are rasterized at',
    resolutionDisabledTitle: 'Resolution applies to PDF input only',
    unwarp: 'Unwarp',
    unwarpTitle: 'Flatten a photographed page before detecting text',
    language: 'Language',
  },
  dialog: {
    discardTitle: 'Discard your edits?',
    discardMessage:
      'Changing this setting re-runs the detection on the whole document. The boxes you added or removed by hand will be lost.',
    reanalyze: 'Re-analyze',
    cancel: 'Cancel',
  },
  editor: {
    pageAlt: 'Document page',
    canvas: 'Redaction box editor',
  },
  debug: {
    button: 'Debug log',
    buttonTitle: 'Re-run detection and show why each box was suggested',
    fetching: 'Collecting…',
    title: 'Detection log',
    empty: 'The detection produced no log for this document.',
    copy: 'Copy',
    copied: 'Copied',
    download: 'Download',
    close: 'Close',
  },
  errors: {
    analyzeFailed: 'Could not analyze the file. Is the service running?',
    renderFailed: 'Could not render the output.',
    debugFailed: 'Could not fetch the detection log.',
    unsupportedType: 'Please choose a PNG, JPEG, or PDF file.',
    tooLarge: `Image is too large (max ${MAX_UPLOAD_MB} MB).`,
  },
}
