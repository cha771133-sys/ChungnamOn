from django.urls import path

from .views import AgencyLookupView

app_name = 'sources'

urlpatterns = [path('agencies/lookup/', AgencyLookupView.as_view(), name='agency-lookup')]
