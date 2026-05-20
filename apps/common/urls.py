from django.urls import path

from .views import DeepHealthView, HealthView

urlpatterns = [
    path("", HealthView.as_view(), name="health"),
    path("deep/", DeepHealthView.as_view(), name="health-deep"),
]
