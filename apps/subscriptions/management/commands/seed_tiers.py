"""Seed default TierConfig rows. Idempotent — safe to run multiple times."""
from django.core.management.base import BaseCommand

from apps.subscriptions.constants import DEFAULT_TIER_CONFIGS
from apps.subscriptions.models import TierConfig


class Command(BaseCommand):
    help = "Seed default tier configurations (free_guest, free_subscribed, mini, max)."

    def handle(self, *args, **options) -> None:
        for cfg in DEFAULT_TIER_CONFIGS:
            obj, created = TierConfig.objects.update_or_create(
                tier=cfg["tier"],
                defaults={k: v for k, v in cfg.items() if k != "tier"},
            )
            verb = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f"  {verb}: {obj.tier} ({obj.label})"))
        self.stdout.write(self.style.SUCCESS("Tier configs seeded."))
