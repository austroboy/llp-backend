"""Load the new Labour Act/Rules docx corpus.

Replaces the old `load_corpus` (which loaded the legacy llp-chat-data6.zip
JSON bundle) with a docx-driven flow. Use this when Tanbhir bhai sends
fresh OCR'd .docx files.

Usage:
    python manage.py load_docx_corpus --dir /path/to/docx_folder
    python manage.py load_docx_corpus --dir /tmp/labour_docs --wipe
    python manage.py load_docx_corpus --dir /tmp/labour_docs --no-embed   # parse only, skip embeddings

The --wipe flag deletes every existing Document/Node row before loading,
so retrieval doesn't pick up stale content from the previous corpus.
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.documents.ingestion_docx import ingest_labour_docx
from apps.documents.models import Document, DocumentVersion, Node


# (filename, doc_code, title, instrument_type, instrument_number)
# doc_codes match the legacy corpus so admins, prompts and crossrefs that
# reference DOC-XXX continue to work.
CORPUS_PLAN = [
    (
        "Bangladesh_Labour_Act__2006_-_OCR.docx",
        "DOC-010",
        "Bangladesh Labour Act, 2006",
        "Act",
        "Act No. XLII of 2006",
    ),
    (
        "Bangladesh_Labour_Rules__2015_-_OCR.docx",
        "DOC-007",
        "Bangladesh Labour Rules, 2015",
        "Rules",
        "S.R.O. No. 291 of 2015",
    ),
    (
        "Bangladesh_Labour__Amendment__Act__2009_-_OCR.docx",
        "DOC-002",
        "Bangladesh Labour (Amendment) Act, 2009",
        "Amendment",
        "Act No. XXX of 2009",
    ),
    (
        "Bangladesh_Labour__Amendment__Act__2010_-_OCR.docx",
        "DOC-003",
        "Bangladesh Labour (Amendment) Act, 2010",
        "Amendment",
        "Act No. 32 of 2010",
    ),
    (
        "Bangladesh_Labour__Amendment__Act__2013___Authentic_English_Translation__-_OCR.docx",
        "DOC-004",
        "Bangladesh Labour (Amendment) Act, 2013",
        "Amendment",
        "Act No. XXX of 2013",
    ),
    (
        "Bangladesh_Labour__Amendment__Act__2018_-_OCR.docx",
        "DOC-005",
        "Bangladesh Labour (Amendment) Act, 2018",
        "Amendment",
        "Act No. XLI of 2018",
    ),
    (
        "Bangladesh_Labour_Rules__Amendment___2022_-_OCR.docx",
        "DOC-008",
        "Bangladesh Labour Rules (Amendment), 2022",
        "Rules",
        "S.R.O. of 2022",
    ),
    (
        "Bangladesh_Labour__Amendment__Ordinance__2025_-_OCR.docx",
        "DOC-006",
        "Bangladesh Labour (Amendment) Ordinance, 2025",
        "Ordinance",
        "Ordinance of 2025",
    ),
    (
        "Bangladesh_Labour__Amendment__Act__2026_-_OCR.docx",
        "DOC-011",
        "Bangladesh Labour (Amendment) Act, 2026",
        "Amendment",
        "Act of 2026",
    ),
]


class Command(BaseCommand):
    help = "Load the Labour Act/Rules docx corpus (9 files) into Documents/Nodes."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dir",
            required=True,
            help="Directory containing the 9 OCR'd .docx files",
        )
        parser.add_argument(
            "--wipe",
            action="store_true",
            help="Delete all existing Documents/Versions/Nodes before loading.",
        )
        parser.add_argument(
            "--no-embed",
            action="store_true",
            help="Skip embedding generation (parse + insert nodes only).",
        )
        parser.add_argument(
            "--only",
            default="",
            help="Comma-separated DOC codes to load (e.g. DOC-010,DOC-007). "
                 "Default: all 9.",
        )

    def handle(self, *args, **options) -> None:
        base = Path(options["dir"]).expanduser().resolve()
        if not base.is_dir():
            raise CommandError(f"--dir is not a directory: {base}")

        only = {c.strip() for c in options["only"].split(",") if c.strip()}
        do_embed = not options["no_embed"]

        # Validate all files exist before we destructively wipe anything.
        missing: list[str] = []
        plan: list[tuple] = []
        for fname, code, title, itype, inum in CORPUS_PLAN:
            if only and code not in only:
                continue
            p = base / fname
            if not p.is_file():
                missing.append(fname)
            else:
                plan.append((p, code, title, itype, inum))
        if missing:
            raise CommandError(
                "Missing files in --dir:\n  " + "\n  ".join(missing)
            )
        if not plan:
            raise CommandError("No files selected (check --only).")

        if options["wipe"]:
            self.stdout.write(self.style.WARNING(
                "Wiping existing Documents / DocumentVersions / Nodes…"
            ))
            with transaction.atomic():
                Node.objects.all().delete()
                DocumentVersion.objects.all().delete()
                Document.objects.all().delete()

        total = {
            "files": 0, "sections": 0, "nodes_created": 0,
            "embeddings_attached": 0, "embedding_errors": 0,
        }
        for p, code, title, itype, inum in plan:
            self.stdout.write(self.style.NOTICE(f"\n→ {code}: {title}"))
            self.stdout.write(f"  source: {p.name}")
            summary = ingest_labour_docx(
                p,
                doc_code=code,
                title=title,
                instrument_type=itype,
                instrument_number=inum,
                language="en",
                embed=do_embed,
            )
            for k, v in summary.items():
                self.stdout.write(f"  {k}: {v}")
            total["files"] += 1
            total["sections"] += summary["sections"]
            total["nodes_created"] += summary["nodes_created"]
            total["embeddings_attached"] += summary["embeddings_attached"]
            total["embedding_errors"] += summary["embedding_errors"]

        self.stdout.write(self.style.SUCCESS("\n=== Corpus load complete ==="))
        for k, v in total.items():
            self.stdout.write(f"  {k}: {v}")
