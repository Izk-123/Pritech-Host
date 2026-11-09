from django.core.management.base import BaseCommand
from core.models import SiteSettings


class Command(BaseCommand):
    help = "Create the SiteSettings singleton with defaults."

    def handle(self, *args, **kwargs):
        site = SiteSettings.load()
        self.stdout.write(self.style.SUCCESS(f"SiteSettings ready: {site}"))