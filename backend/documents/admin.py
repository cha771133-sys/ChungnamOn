from django.contrib import admin

from chatbot.models import ChatMessage

from .models import ChecklistItem, Document, InterpretationResult, Schedule


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


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    """조회 전용. 삭제 권한은 문서·사용자 삭제의 CASCADE가 막히지 않도록 기본값을 유지한다."""

    list_display = ('id', 'owner', 'document', 'title', 'date', 'done', 'done_at', 'created_at')
    list_filter = ('done', 'date')
    list_select_related = ('owner', 'document')
    readonly_fields = ('owner', 'document', 'title', 'date', 'done', 'done_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    """조회 전용. 삭제 권한은 문서 삭제의 CASCADE가 막히지 않도록 기본값을 유지한다."""

    list_display = ('id', 'document', 'order', 'text', 'state', 'due', 'done', 'done_at')
    list_filter = ('state', 'done')
    list_select_related = ('document',)
    readonly_fields = (
        'document', 'order', 'text', 'detail', 'docs', 'method', 'due', 'state', 'done', 'done_at',
        'note', 'source_ids', 'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
