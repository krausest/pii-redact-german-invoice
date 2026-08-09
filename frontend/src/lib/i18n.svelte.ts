/**
 * The only shared reactive state in the app — everything else flows through props.
 *
 * Components read the active catalogue with `const m = $derived(t())`; because `t()`
 * touches `i18n.locale`, that derivation re-runs on a locale change and every string
 * in the template follows.
 */
import { de } from './messages/de'
import { en } from './messages/en'

export type Locale = 'en' | 'de'
export type Messages = typeof en

const CATALOGUES: Record<Locale, Messages> = { en, de }
const STORAGE_KEY = 'pii-redact.locale'

function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'de'
}

/** A stored choice wins; otherwise the first browser language we speak; else English. */
function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isLocale(saved)) return saved
  } catch {
    /* localStorage throws in some privacy modes; fall through to detection */
  }
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of tags) {
    const base = tag?.toLowerCase().split('-')[0] // de-AT -> de
    if (isLocale(base)) return base
  }
  return 'en'
}

export const i18n = $state({ locale: detectLocale() })

export function t(): Messages {
  return CATALOGUES[i18n.locale]
}

export function setLocale(next: Locale): void {
  i18n.locale = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* not persisting is survivable; the session still switches */
  }
}
