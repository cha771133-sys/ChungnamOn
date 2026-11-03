from django.contrib import admin

from .models import Document, InterpretationResult


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('id', 'owner', 'file_type', 'status', 'step', 'created_at')
    list_filter = ('status', 'file_type')


@admin.register(InterpretationResult)
class InterpretationResultAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'doc_type', 'title', 'language', 'created_at')
    list_select_related = ('document',)
