from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django Admin (SRS FR-41: Staff and admin tools)
    path('admin/', admin.site.urls),
    
    # Authentication URLs (Login, Logout, Google Sign-in, Password Reset)
    path('accounts/', include('allauth.urls')),
    
    # Core App URLs (Home, Checkout, Payments)
    path('', include('core.urls')),
]

# Serve static and media files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)