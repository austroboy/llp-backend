from django.urls import path

from .views import (
    AuditEventListView,
    ChainIntegrityView,
    CostDashboardView,
    SystemSummaryView,
)

urlpatterns = [
    path("audit/", AuditEventListView.as_view(), name="audit-list"),
    path("audit/integrity/", ChainIntegrityView.as_view(), name="audit-integrity"),
    path("cost/", CostDashboardView.as_view(), name="cost-dashboard"),
    path("summary/", SystemSummaryView.as_view(), name="admin-summary"),
]
