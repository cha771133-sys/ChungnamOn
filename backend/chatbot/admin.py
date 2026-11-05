from django.contrib import admin

from .models import ChatMessage


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'document_id', 'language', 'grounded', 'status', 'created_at')
    list_filter = ('language', 'grounded', 'status')
    # 권한 있는 관리자도 목록/검색에서 대화 전문을 노출하지 않는다.
    search_fields = ()
    fields = ('id', 'document_id', 'language', 'grounded', 'status', 'created_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
