from django.urls import path

from .views import (
    CitationAuditListView,
    CitationAuditResolveView,
    DocumentDetailView,
    DocumentListView,
    DocumentUploadView,
    IngestionJobView,
    NodeDetailView,
    NodeListView,
    SidebarView,
)

urlpatterns = [
    path("", DocumentListView.as_view(), name="documents-list"),
    path("upload/", DocumentUploadView.as_view(), name="documents-upload"),
    path("jobs/<int:job_id>/", IngestionJobView.as_view(), name="ingestion-job"),
    path("citation-audits/", CitationAuditListView.as_view(), name="citation-audit-list"),
    path("citation-audits/<int:audit_id>/resolve/",
         CitationAuditResolveView.as_view(), name="citation-audit-resolve"),
    path("nodes/<str:node_id>/", NodeDetailView.as_view(), name="node-detail"),
    path("sidebar/<str:node_id>/", SidebarView.as_view(), name="sidebar"),
    path("<str:doc_code>/", DocumentDetailView.as_view(), name="document-detail"),
    path("<str:doc_code>/nodes/", NodeListView.as_view(), name="document-nodes"),
]
