from django.urls import path

from .views import DeviceDeactivateView, DeviceRegistrationView, PreferenceView

app_name = 'notifications'

urlpatterns = [
    path('devices/', DeviceRegistrationView.as_view(), name='device-register'),
    path('devices/<int:pk>/', DeviceDeactivateView.as_view(), name='device-deactivate'),
    path('preferences/', PreferenceView.as_view(), name='preferences'),
]
