from django.urls import path

from app.api.views_health import health

urlpatterns = [
    path("api/health/", health, name="health"),
]
