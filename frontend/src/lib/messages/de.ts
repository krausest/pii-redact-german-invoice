import type { Messages } from '../i18n.svelte'
import { MAX_UPLOAD_BYTES } from '../types'

const MAX_UPLOAD_MB = Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024)

// The `: Messages` annotation is the safety net: a key missing here, misspelled, or
// carrying the wrong signature fails `npm run check`.
export const de: Messages = {
  app: {
    documentTitle: 'PII-Schwärzung',
    title: 'PII-Schwärzung',
    subtitle:
      'Laden Sie eine Rechnung als Bild oder PDF hoch. Prüfen Sie die vorgeschlagenen Schwärzungen, ergänzen oder entfernen Sie Markierungen und laden Sie das Ergebnis herunter.',
    analyzing: 'Analyse — Seiten werden entzerrt und PII erkannt…',
    hintDraw: 'Ziehen Sie auf der Seite, um eine Schwärzung aufzuziehen.',
    hintSelect: 'Klicken Sie eine Markierung an und drücken Sie Entf, um sie zu entfernen.',
    boxCount: (n: number) => `${n} Markierung${n === 1 ? '' : 'en'} auf dieser Seite`,
  },
  toolbar: {
    tools: 'Werkzeuge',
    select: 'Auswählen',
    drawBox: 'Markierung zeichnen',
    delete: 'Löschen',
    deleteSelected: 'Ausgewählte Markierung löschen',
    pageNavigation: 'Seitennavigation',
    previousPage: 'Vorherige Seite',
    nextPage: 'Nächste Seite',
    // Kept short on purpose — the toolbar has to stay on one line.
    download: 'Download',
    rendering: 'Wird erstellt…',
    downloadPdf: 'Geschwärztes PDF herunterladen',
    downloadImage: 'Geschwärztes Bild herunterladen',
  },
  filedrop: {
    dropBefore: 'PNG-, JPEG- oder PDF-Datei ',
    dropStrong: 'hierher ziehen',
    dropAfter: '',
    orClick: 'oder klicken, um eine Datei auszuwählen',
    newUpload: 'Neue Datei',
    inlineTitle: 'Neue Datei hochladen — klicken oder hierher ziehen',
  },
  settings: {
    group: 'Analyse-Einstellungen',
    resolution: 'Auflösung',
    resolutionTitle: 'Auflösung, mit der PDF-Seiten gerastert werden',
    resolutionDisabledTitle: 'Die Auflösung gilt nur für PDF-Dateien',
    unwarp: 'Entzerren',
    unwarpTitle: 'Fotografierte Seite vor der Texterkennung begradigen',
    language: 'Sprache',
  },
  dialog: {
    discardTitle: 'Änderungen verwerfen?',
    discardMessage:
      'Wird diese Einstellung geändert, läuft die Erkennung für das gesamte Dokument neu. Die von Hand ergänzten oder entfernten Markierungen gehen dabei verloren.',
    reanalyze: 'Neu analysieren',
    cancel: 'Abbrechen',
  },
  editor: {
    pageAlt: 'Dokumentseite',
    canvas: 'Editor für Schwärzungen',
  },
  debug: {
    button: 'Debug-Protokoll',
    buttonTitle: 'Erkennung erneut ausführen und anzeigen, warum jede Markierung vorgeschlagen wurde',
    fetching: 'Wird erfasst…',
    title: 'Erkennungsprotokoll',
    empty: 'Die Erkennung hat für dieses Dokument kein Protokoll erzeugt.',
    copy: 'Kopieren',
    copied: 'Kopiert',
    download: 'Herunterladen',
    close: 'Schließen',
  },
  errors: {
    analyzeFailed: 'Die Datei konnte nicht analysiert werden. Läuft der Dienst?',
    renderFailed: 'Die Ausgabe konnte nicht erzeugt werden.',
    debugFailed: 'Das Erkennungsprotokoll konnte nicht geladen werden.',
    unsupportedType: 'Bitte wählen Sie eine PNG-, JPEG- oder PDF-Datei.',
    tooLarge: `Die Datei ist zu groß (max. ${MAX_UPLOAD_MB} MB).`,
  },
}
