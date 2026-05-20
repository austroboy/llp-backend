from django.urls import path

from .views import CancelView, MeSubscriptionView, QuotaView, TierListView, UpgradeView

urlpatterns = [
    path("tiers/", TierListView.as_view(), name="tier-list"),
    path("me/", MeSubscriptionView.as_view(), name="my-subscription"),
    path("quota/", QuotaView.as_view(), name="quota"),
    path("upgrade/", UpgradeView.as_view(), name="upgrade"),
    path("cancel/", CancelView.as_view(), name="cancel"),
]
