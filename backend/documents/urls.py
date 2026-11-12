from django.urls import path

from . import views

urlpatterns = [
    path('', views.DocumentListCreateView.as_view(), name='document-list'),
    path('<int:pk>/', views.DocumentDetailView.as_view(), name='document-detail'),
    path('<int:pk>/status/', views.DocumentStatusView.as_view(), name='document-status'),
    path('<int:pk>/result/', views.DocumentResultView.as_view(), name='document-result'),
    path('<int:pk>/verification/', views.DocumentVerificationView.as_view(), name='document-verification'),
    path('<int:pk>/checklist/', views.DocumentChecklistView.as_view(), name='document-checklist'),
    path(
        '<int:pk>/checklist/<int:item_id>/',
        views.DocumentChecklistItemView.as_view(),
        name='document-checklist-item',
    ),
]
