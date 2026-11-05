from django.contrib import admin

from chatbot.models import ChatMessage

from .models import Document, InterpretationResult


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('id', 'owner', 'file_type', 'status', 'step', 'created_at')
    list_filter = ('status', 'file_type')

    def get_deleted_objects(self, objs, request):
        """부모 삭제 권한은 유지하고 ChatMessage의 CASCADE에만 직접 삭제 금지를 적용하지 않는다."""
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        permissions.discard(ChatMessage._meta.verbose_name)
        return deleted, counts, permissions, protected


@admin.register(InterpretationResult)
class InterpretationResultAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'doc_type', 'title', 'language', 'created_at')
    list_select_related = ('document',)
