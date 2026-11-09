from .models import SiteSettings


def site_settings(request):
    """Expose SiteSettings to every template."""
    return {'site': SiteSettings.load()}