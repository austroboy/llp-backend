from django.urls import path

from .views import (
    ConversationDetailView,
    ConversationListCreateView,
    FileUploadView,
    QuotaCheckView,
    SendMessageView,
)

urlpatterns = [
    path("conversations/", ConversationListCreateView.as_view(), name="conversation-list"),
    path("conversations/<int:conv_id>/", ConversationDetailView.as_view(), name="conversation-detail"),
    path("conversations/<int:conv_id>/messages/", SendMessageView.as_view(), name="send-message"),
    path("conversations/<int:conv_id>/files/", FileUploadView.as_view(), name="file-upload"),
    path("quota/check/", QuotaCheckView.as_view(), name="chat-quota-check"),
]
