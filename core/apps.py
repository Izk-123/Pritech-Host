from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        """
        Register signal handlers. Importing ``core.signals`` here wires
        up the ``user_signed_up`` receiver that records legal acceptance
        at signup time.
        """
        import core.signals  # noqa: F401