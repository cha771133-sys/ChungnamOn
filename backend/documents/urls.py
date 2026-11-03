from django.urls import path

from . import views

urlpatterns = [
    path('', views.DocumentUploadView.as_view(), name='document-upload'),
    path('<int:pk>/status/', views.DocumentStatusView.as_view(), name='document-status'),
]
