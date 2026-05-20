from django.urls import path

from .views import (
    InvoiceListView,
    ManualConfirmInvoiceView,
    SSLCommerzWebhookView,
    StripeWebhookView,
)

urlpatterns = [
    path("invoices/", InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<int:invoice_id>/confirm/",
         ManualConfirmInvoiceView.as_view(), name="invoice-manual-confirm"),
    path("webhooks/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),
    path("webhooks/sslcommerz/", SSLCommerzWebhookView.as_view(), name="sslcommerz-webhook"),
]
