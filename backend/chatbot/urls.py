from django.urls import path

from .views import DocumentChatView

app_name = 'chatbot'
urlpatterns = [path('<int:pk>/chat/', DocumentChatView.as_view(), name='document-chat')]
