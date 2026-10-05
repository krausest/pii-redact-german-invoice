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
      'Laden Sie Rechnungen als Bild oder PDF hoch — mehrere auf einmal oder als ZIP. Prüfen Sie die vorgeschlagenen Schwärzungen, ergänzen oder entfernen Sie Markierungen und laden Sie das Ergebnis herunter.',
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
    downloadAll: 'ZIP',
    downloadAllTitle: 'Alle fertigen Dokumente als ein ZIP herunterladen',
    downloadAllWaiting: 'Verfügbar, sobald alle Dokumente analysiert sind',
  },
  filedrop: {
    dropBefore: 'PNG-, JPEG-, PDF- oder ZIP-Dateien ',
    dropStrong: 'hierher ziehen',
    dropAfter: '',
    orClick: 'oder klicken, um Dateien auszuwählen',
    newUpload: 'Neuer Upload',
    inlineTitle: 'Aktuelle Dokumente ersetzen — klicken oder hierher ziehen',
  },
  settings: {
    group: 'Analyse-Einstellungen',
    resolution: 'Auflösung',
    resolutionTitle: 'Auflösung, mit der PDF-Seiten gerastert werden',
    resolutionDisabledTitle: 'Die Auflösung gilt nur für PDF-Dateien',
    unwarp: 'Entzerren',
    unwarpTitle: 'Fotografierte Seite vor der Texterkennung begradigen',
    classifier: 'Modell',
    classifierTitle: 'Das Modell, das personenbezogene Daten im erkannten Text findet',
    language: 'Sprache',
  },
  dialog: {
    discardTitle: 'Änderungen verwerfen?',
    discardMessage:
      'Wird diese Einstellung geändert, läuft die Erkennung für das gesamte Dokument neu. Die von Hand ergänzten oder entfernten Markierungen gehen dabei verloren.',
    reanalyze: 'Neu analysieren',
    cancel: 'Abbrechen',
    discardAllTitle: 'Alle Dokumente verwerfen?',
    discardAllMessage: 'Einige Dokumente wurden seit der letzten Änderung nicht heruntergeladen. Sie gehen verloren.',
    discardAllConfirm: 'Alle verwerfen',
    removeTitle: 'Dokument entfernen?',
    removeMessage: (name: string) => `${name} wurde seit der letzten Änderung nicht heruntergeladen und geht verloren.`,
    removeConfirm: 'Entfernen',
    replaceTitle: 'Aktuelle Dokumente ersetzen?',
    replaceMessage: (n: number) =>
      `${n} Dokument${n === 1 ? ' wurde' : 'e wurden'} seit der letzten Änderung nicht heruntergeladen und ${n === 1 ? 'geht' : 'gehen'} verloren.`,
    replaceConfirm: 'Ersetzen',
  },
  docs: {
    list: 'Dokumente',
    progress: (done: number, total: number) => `${done} / ${total} analysiert`,
    status: {
      queued: 'Wartet',
      analyzing: 'Wird analysiert',
      ready: 'Fertig',
      error: 'Fehlgeschlagen',
    },
    edited: 'Von Hand bearbeitet',
    remove: 'Entfernen',
    removeNamed: (name: string) => `${name} entfernen`,
    discardAll: 'Alle verwerfen',
    waiting: 'Wartet — die Dokumente werden nacheinander analysiert.',
    retry: 'Erneut versuchen',
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
    badZip: 'Die ZIP-Datei konnte nicht gelesen werden.',
    emptyZip: 'Die ZIP-Datei enthält keine PNG-, JPEG- oder PDF-Dateien.',
    rejected: (items: string[]) => `Es wurde nichts hochgeladen. Nicht verarbeitbar: ${items.join('; ')}`,
    zipFailed: 'Das ZIP konnte nicht erstellt werden.',
    zipSkipped: (n: number) =>
      `${n} Dokument${n === 1 ? ' konnte' : 'e konnten'} nicht erzeugt werden und ${n === 1 ? 'fehlt' : 'fehlen'} im ZIP.`,
  },
}
