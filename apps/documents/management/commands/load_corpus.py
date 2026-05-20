"""Load the production corpus from llp-chat-data6.zip (or compatible).

Usage:
    python manage.py load_corpus --zip /path/to/llp-chat-data6.zip
"""
from django.core.management.base import BaseCommand, CommandError

from apps.documents.ingestion import ingest_corpus_zip


class Command(BaseCommand):
    help = "Bulk-load a corpus zip (DOC-XXX.json + indexes + embeddings)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--zip", required=True, help="Path to the corpus zip file")

    def handle(self, *args, **options) -> None:
        path = options["zip"]
        self.stdout.write(f"Loading corpus from {path}…")
        try:
            summary = ingest_corpus_zip(path)
        except Exception as e:  # noqa: BLE001
            raise CommandError(f"Ingestion failed: {e}") from e

        self.stdout.write(self.style.SUCCESS("Done."))
        for k, v in summary.items():
            self.stdout.write(f"  {k}: {v}")
