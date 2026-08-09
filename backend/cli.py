"""Batch CLI: redact jpg/jpeg/png/pdf files or directories in place.

Replaces the three standalone ``redact_*.py`` scripts; the engine comes from the
config file (or ``PII_ENGINE``) and is fixed for the run.

    uv run pii-redact Arztrechnung/
    uv run pii-redact example/GOÄ_Rechnung1.pdf other.png
    uv run pii-redact --no-unwarp --pdf-dpi 300 example/GOÄ_Rechnung1.pdf

Everything past collecting the files is :mod:`backend.service`, the same
composition ``POST /api/redact`` runs — so a PDF handed to the CLI comes back as
the identical document the API would return. Output is written next to each input
as ``<stem>_redacted.pdf`` for a PDF and ``<stem>_redacted.jpg`` for an image
(images always come back as JPEG), or ``<stem>_redacted.json`` with
``--json-output``.

**The flags are the query parameters.** Every ``POST /api/redact`` option is a
flag of the same (hyphenated) name, and they are validated by the same
:class:`~backend.options.RedactOptions` — argparse only collects strings, so a bad
value fails with the message the API would have returned as its ``400`` detail. A
flag left off is absent from the query, which is what makes ``config.toml`` the
single home of the defaults for both callers.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from backend.config import load_config
from backend.factory import build_pipeline
from backend.options import RedactOptions
from backend.service import EXTENSION_BY_MEDIA_TYPE, produce_output, run_redaction

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}
_REDACTED = re.compile(r".*_redacted.*")


def collect_input_files(paths: list[str]) -> list[Path]:
    """Resolve CLI arguments to a flat list of supported files. A file argument
    is passed through as-is; a directory is scanned one level deep (no recursion
    into subdirectories). Already-redacted outputs are skipped."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if (
                    child.is_file()
                    and child.suffix.lower() in SUPPORTED_SUFFIXES
                    and not _REDACTED.search(child.name)
                ):
                    files.append(child)
        elif p.is_file() and not _REDACTED.search(p.name):
            if p.suffix.lower() in SUPPORTED_SUFFIXES:
                files.append(p)
            else:
                print(f"Warning: unsupported file type, skipping: {p}", file=sys.stderr)
        else:
            print(f"Warning: path not found, skipping: {p}", file=sys.stderr)
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Redact PII from German invoice images.")
    parser.add_argument("paths", nargs="+", help="jpg/jpeg/png/pdf files or directories")

    # One flag per `POST /api/redact` query parameter, same name. The defaults are
    # deliberately `None`/`False` rather than the config values: an untouched flag
    # must stay out of the query so `RedactOptions.from_query` fills it from the
    # config, exactly as it does for a request that omitted the parameter. Values
    # stay strings so pydantic — not argparse — reports what is wrong with them.
    opts = parser.add_argument_group("redaction options (POST /api/redact query parameters)")
    opts.add_argument(
        "--unwarp",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="flatten the photographed page before OCR (default: redaction.unwarp)",
    )
    opts.add_argument(
        "--json-output",
        action="store_true",
        default=None,
        help="write the JSON report to <stem>_redacted.json instead of the document",
    )
    opts.add_argument(
        "--pdf-dpi",
        metavar="N",
        help="rasterization DPI for PDF input, 36-1200 (default: redaction.pdf_dpi)",
    )
    opts.add_argument(
        "--jpeg-quality",
        metavar="N",
        help="quality of every JPEG produced, 1-100 (default: redaction.jpeg_quality)",
    )
    opts.add_argument(
        "--debug",
        action="store_true",
        default=None,
        help="include the per-line detection trace in the report (needs --json-output)",
    )
    return parser


def query_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """The flags that were actually given, keyed by their query-parameter name.

    Read off ``RedactOptions`` rather than hand-listed: argparse stores ``--pdf-dpi``
    as ``pdf_dpi`` and the wire name is the same field with hyphens, so the mapping
    is mechanical and does not need a third copy of the option names. Every flag
    defaults to ``None``, and those are dropped here, so an untouched flag stays out
    of the query and ``from_query`` fills it from the config — the one place a
    default may come from.
    """
    return {
        name: value
        for name in RedactOptions.wire_names()
        if (value := getattr(args, name.replace("-", "_"))) is not None
    }


def main(argv: list[str] | None = None) -> int:
    # PII_LOG_LEVEL=DEBUG logs every OCR line, its box, each classifier match and
    # the redact verdict. Bare messages: for an interactive CLI the debug stream
    # *is* the output, so no timestamp/level prefix noise.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    logging.getLogger("backend").setLevel(os.environ.get("PII_LOG_LEVEL", "INFO").upper())

    args = build_parser().parse_args(argv)

    input_files = collect_input_files(args.paths)
    if not input_files:
        print("Error: no jpg/jpeg/png/pdf files found in the given paths.", file=sys.stderr)
        return 1

    config = load_config()
    try:
        opts = RedactOptions.from_query(query_from_args(args), config)
    except ValueError as e:
        # The same one-line detail the API reports as a 400 — a bad --pdf-dpi is a
        # usage error, so nothing is processed.
        print(f"Error: {e}", file=sys.stderr)
        return 2

    pipeline = build_pipeline(config)

    failed = 0
    for f in input_files:
        try:
            if f.suffix.lower() == ".pdf":
                redaction = run_redaction(pipeline, f.read_bytes(), opts, config)
            else:
                with Image.open(f) as src:
                    # A phone photo stores its pixels sideways and an EXIF tag saying
                    # so; PIL applies neither. Straighten at the boundary so the raster
                    # is the only truth from here on — OCR sees an upright page, and
                    # the output (JPEG, written without EXIF) needs no tag to display
                    # the way the input did.
                    redaction = run_redaction(pipeline, ImageOps.exif_transpose(src), opts, config)
            # The same bytes POST /api/redact would return, so the extension is the
            # one that belongs to the media type service chose — never re-derived.
            media_type, body = produce_output(redaction, opts)
        except (OSError, ValueError) as e:
            # Unreadable file, or a rasterization guard (too many pages, oversized
            # page). One bad file must not abort the rest of the batch.
            print(f"Error: {f.name}: {e}", file=sys.stderr)
            failed += 1
            continue
        out_path = f.with_name(f.stem + "_redacted" + EXTENSION_BY_MEDIA_TYPE[media_type])
        out_path.write_bytes(body)
        print(f"Redacted {f.name} -> {out_path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
