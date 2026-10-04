# Web UI

A single-page app in [`frontend/`](../frontend/): Svelte 5, TypeScript, Vite. It
talks to the [REST API](API.md) directly and keeps all state in the browser tab —
the server stores nothing.

## Flow

1. **Upload** one or more PNG/JPEG/PDF files, or a ZIP of them (drop or file
   picker). A ZIP is unpacked in the browser; if anything in an upload cannot be
   processed, the whole upload is rejected with a list of the offending files.
2. **Analyze:** each document goes to `POST /api/redact?json-output=true`, one
   after another. The response holds the clean page images and suggested boxes.
3. **Review:** draw, select and delete boxes on each page. With several documents a
   side list shows their progress; finished ones open instantly.
4. **Download:** `POST /api/assemble` turns the pages and kept boxes into the final
   PDF or JPEG — per document, or all finished documents as one ZIP.

Resolution, dewarping and classifier are set per document; changing one re-runs
the analysis for that document (asking first if boxes were edited). The footer's
*Debug log* re-runs detection with `debug=true` and shows the trace without
touching your edits.

*New upload* replaces the loaded documents. Nothing survives a reload either. The
app asks before replacing, removing or discarding — and the browser warns before
leaving — only while a document would be lost: still being analyzed, or not
downloaded (alone or in a ZIP) since its last change.

## Code map

| File | Role |
|---|---|
| `src/App.svelte` | state and wiring |
| `src/lib/api.ts` | the three API calls (`analyze`, `render`, `fetchDebugLog`) and `/health` |
| `src/lib/documents.svelte.ts` | document list and the sequential analysis queue |
| `src/lib/zip.ts` | ZIP unpack/pack ([fflate](https://github.com/101arrowz/fflate)) |
| `src/lib/PageEditor.svelte` | box editor over one page image |
| `src/lib/DocumentList.svelte`, `Toolbar.svelte`, `Settings.svelte`, `FileDrop.svelte` | UI parts |
| `src/lib/i18n.svelte.ts`, `src/lib/messages/{en,de}.ts` | translations; `de` is type-checked against `en` |

## Commands

```bash
cd frontend
npm install
npm run dev     # :5173, proxies /api and /health to 127.0.0.1:8000
npm run check   # type check (also catches missing translations)
npm run build   # -> dist/, served by the API via PII_STATIC_DIR
```

See [Running](Running.md) for the full dev and production setup.
