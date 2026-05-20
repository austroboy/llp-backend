"""Set up the pgvector extension. Runs first, before any app that needs vectors."""
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list = []

    operations = [
        CreateExtension("vector"),
        CreateExtension("pg_trgm"),
        CreateExtension("citext"),
    ]
