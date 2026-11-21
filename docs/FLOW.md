# Flow

One page on its way from upload to redacted file. Details per stage are in
[Redaction](Redaction.md).

```mermaid
flowchart TD
    IN["Upload: PNG / JPEG / PDF"] --> RAS["Rasterize PDF at pdf-dpi<br/>EXIF rotation"]
    RAS --> UNW{"unwarp?"}
    UNW -- yes --> DEW["Dewarp page"] --> OCR
    UNW -- no --> OCR
    OCR["PaddleOCR<br/>lines + word boxes"] --> FOLD["Fold diacritics<br/>É→E, í→i · keeps äöüß"]
    FOLD --> LAY["PP-DocLayout regions"]
    LAY --> ORD["Reading order → page text"]
    ORD --> KEEP["Item table + kept lines<br/>DATE · DIAG · PVS · FACH"]
    KEEP --> DET
    subgraph DET ["Candidates"]
        RULES["Regex rules<br/>line rules · imprint values · labeled values"]
        CLS["Classifier<br/>presidio / guard-omni"]
        POS["Positive word list<br/>exact + OCR variants"]
    end
    DET --> SIFT["Sift: keep reasons · negative list veto"]
    SIFT --> HARV["Harvest name + birthdate memory"]
    HARV --> REC["Recall: remembered names + OCR variants,<br/>remembered birthdates"]
    REC --> SIFT2["Sift recalled"]
    SIFT2 --> WB["Boxes over redacted word runs"]
    WB --> EXTRA["+ birth-year notes · region boxes · signature boxes"]
    EXTRA --> REP["JSON report: page images + boxes"]
    REP --> UI["Web UI: review and edit boxes"]
    UI --> ASM["POST /api/assemble: fill boxes → PDF / JPEG"]
```

| Stage | Where |
|---|---|
| Rasterize, EXIF | [`pdf.py`](../backend/pdf.py), [`api.py`](../backend/api.py) |
| Dewarp | [`unwarp.py`](../backend/unwarp.py) |
| OCR | [`ocr/paddle.py`](../backend/ocr/paddle.py) |
| Fold diacritics | [`textnorm.py`](../backend/textnorm.py) |
| Layout regions, reading order | [`layout.py`](../backend/layout.py) |
| Item table, kept lines | [`rules/item_table.py`](../backend/rules/item_table.py), [`rules/keep.py`](../backend/rules/keep.py) |
| Regex rules | [`rules/`](../backend/rules/) |
| Classifier | [`classifiers/`](../backend/classifiers/) |
| Word lists | [`wordlists.py`](../backend/wordlists.py) |
| Sift, boxes | [`redactor.py`](../backend/redactor.py), [`detectors.py`](../backend/detectors.py) |
| Name and birthdate memory | [`harvest.py`](../backend/harvest.py) |
| OCR variants | [`ocr_variants.py`](../backend/ocr_variants.py) |
| Report, assemble | [`service.py`](../backend/service.py) |
| Web UI | [`frontend/`](../frontend/) |
