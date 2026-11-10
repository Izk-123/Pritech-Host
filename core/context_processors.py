"""
Template context processors for the core app.

Registered in ``TEMPLATES[0]['OPTIONS']['context_processors']`` in
settings.py so that every template automatically has these variables
in scope.

Two independent processors, one concern each:

    site_settings    — SiteSettings singleton (hero copy, contact info,
                       support hours, cPanel URL, footer tagline).

    legal_documents  — Current active version of each legal document
                       (Terms, Privacy, AUP, Refund), exposed so the
                       footer, checkout, and signup pages can link to
                       real versioned URLs instead of hardcoded paths.

Performance notes
-----------------
* ``SiteSettings.load()`` is already cached in the model layer with a
  10-minute TTL and self-invalidates on save(), so the ``site_settings``
  processor is effectively free after the first call per worker.

* ``LegalDocument.current()`` does one DB query per call. Since
  ``legal_documents()`` would fire four of those on every page render,
  we batch the four lookups into a single cache entry with a 60-second
  TTL. That trades at most 60 s of staleness after an admin edit for a
  large reduction in query load on the LegalDocument table.
"""

from django.core.cache import cache

from .models import SiteSettings


# ---------------------------------------------------------------------------
# Cache configuration for legal_documents()
# ---------------------------------------------------------------------------
# Adjust the TTL if your legal edit workflow needs faster propagation:
#   - 60s   : admin edits appear within a minute (current choice)
#   - 300s  : five-minute delay, slightly fewer cache misses
#   - 0     : disable caching entirely (4 DB queries per page — not recommended)
#
# If you want *instant* propagation, add ``cache.delete(LEGAL_DOCS_CACHE_KEY)``
# to ``LegalDocument.save()`` (or its admin's save_model). The cache would
# then invalidate on every legal edit, and this TTL becomes a safety net
# rather than the primary freshness mechanism.
LEGAL_DOCS_CACHE_KEY = 'core_legal_documents'
LEGAL_DOCS_CACHE_TTL = 60


# =============================================================================
# Site-wide editable content
# =============================================================================

def site_settings(request):
    """
    Inject the SiteSettings singleton into every template as ``site``.

    Templates use this for hero copy, contact details, support hours,
    cPanel URL, footer tagline, and the hero preview card. Admin edits
    these from ``/admin/`` → Site Settings — no deploy required.

    The singleton is cached in the model layer (10-minute TTL) and
    self-invalidates on save(), so this processor is effectively free
    after the first call per worker.
    """
    return {'site': SiteSettings.load()}


# =============================================================================
# Legal documents
# =============================================================================

def legal_documents(request):
    """
    Inject the four active legal documents into every template.

    Template variables exposed:

        legal_terms     : LegalDocument | None   (Terms of Service)
        legal_privacy   : LegalDocument | None   (Privacy Policy)
        legal_aup       : LegalDocument | None   (Acceptable Use Policy)
        legal_refund    : LegalDocument | None   (Refund Policy)

    Each is the current active version for its slug, or ``None`` if no
    document has been published yet. Templates should guard with
    ``{% if legal_terms %}`` before rendering a link so the site doesn't
    404 on a missing document:

        {% if legal_terms %}
            <a href="{% url 'core:legal_document' 'terms' %}">
                {{ legal_terms.title }} v{{ legal_terms.version }}
            </a>
        {% endif %}

    The whole payload is cached as a single entry — 4 DB lookups on
    cache miss, zero on cache hit.
    """
    # Fast path: cached payload.
    cached = cache.get(LEGAL_DOCS_CACHE_KEY)
    if cached is not None:
        return cached

    # Slow path: four DB lookups (only on cache miss).
    #
    # Deferred import — context processors are loaded very early in the
    # Django boot sequence, potentially before model apps are fully
    # ready. Importing at module level risks an import-order error on
    # `manage.py check` or during migration bootstrapping.
    from .models import LegalDocument

    payload = {
        'legal_terms':   LegalDocument.current('terms'),
        'legal_privacy': LegalDocument.current('privacy'),
        'legal_aup':     LegalDocument.current('aup'),
        'legal_refund':  LegalDocument.current('refund'),
    }

    cache.set(LEGAL_DOCS_CACHE_KEY, payload, LEGAL_DOCS_CACHE_TTL)
    return payload