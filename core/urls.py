from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # ==========================================
    # Storefront & Checkout
    # ==========================================
    path('', views.HomeView.as_view(), name='home'),
    path('checkout/<int:plan_id>/', views.CheckoutView.as_view(), name='checkout'),
    path('checkout/success/<int:invoice_id>/', views.CheckoutSuccessView.as_view(), name='checkout_success'),

    # ==========================================
    # Customer Dashboard & APIs
    # ==========================================
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('dashboard/profile/', views.ProfileView.as_view(), name='profile'),

    path('api/dashboard-data/', views.DashboardAPIView.as_view(), name='api_dashboard'),
    path('api/profile-data/', views.ProfileAPIView.as_view(), name='api_profile_data'),
    path('api/pay-invoice/', views.PayInvoiceAPIView.as_view(), name='api_pay_invoice'),
    path('api/create-ticket/', views.CreateTicketAPIView.as_view(), name='api_create_ticket'),
    path('invoices/<int:invoice_id>/pdf/', views.InvoicePDFView.as_view(), name='invoice_pdf'),

    # ==========================================
    # Ticket Conversation APIs (SRS FR-46)
    # ==========================================
    path('api/tickets/<int:ticket_id>/', views.TicketDetailAPIView.as_view(), name='api_ticket_detail'),
    path('api/tickets/<int:ticket_id>/reply/', views.ReplyToTicketAPIView.as_view(), name='api_ticket_reply'),

    # ==========================================
    # Payment Gateway (PayChangu)
    # ==========================================
    path('payments/webhook/', views.PayChanguWebhookView.as_view(), name='paychangu_webhook'),
    path('payments/callback/', views.PaymentCallbackView.as_view(), name='payment_callback'),
    path('payments/return/', views.PaymentReturnView.as_view(), name='payment_return'),

    # ==========================================
    # Legal Documents & Cookie Consent
    # ==========================================
    path('legal/<str:slug>/', views.LegalDocumentView.as_view(), name='legal_document'),
    path('api/cookie-consent/', views.CookieConsentAPIView.as_view(), name='api_cookie_consent'),
]