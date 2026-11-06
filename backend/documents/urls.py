from django.urls import path

from . import views

urlpatterns = [
    path('', views.DocumentListCreateView.as_view(), name='document-list'),
    path('<int:pk>/', views.DocumentDetailView.as_view(), name='document-detail'),
    path('<int:pk>/status/', views.DocumentStatusView.as_view(), name='document-status'),
    path('<int:pk>/result/', views.DocumentResultView.as_view(), name='document-result'),
]
