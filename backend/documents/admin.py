from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('id', 'owner', 'file_type', 'status', 'step', 'created_at')
    list_filter = ('status', 'file_type')
