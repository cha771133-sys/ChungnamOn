from django.contrib import admin

from .models import NotificationPreference, PushDevice


class RestrictedReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_module_permission(self, request):
        """일반 staff를 제외하고 활성 최고 관리자에게만 알림 모듈을 표시한다."""
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        """직접 URL 접근에도 모듈과 같은 최고 관리자 제한을 적용한다."""
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        """관리자 화면에서 기기와 수신 동의를 임의로 생성하지 못하게 한다."""
        return False

    def has_change_permission(self, request, obj=None):
        """운영 조회가 사용자의 수신 의사나 기기 소유권을 변경하지 못하게 한다."""
        return False

    def has_delete_permission(self, request, obj=None):
        """관리자 화면에서 알림 관련 기록을 삭제하지 못하게 한다."""
        return False


@admin.register(PushDevice)
class PushDeviceAdmin(RestrictedReadOnlyAdmin):
    list_display = ['id', 'user_id', 'registration_type', 'platform', 'is_active', 'last_success_at', 'updated_at']
    list_filter = ['is_active', 'platform']
    fields = ['id', 'user', 'registration_type', 'platform', 'is_active', 'created_at', 'updated_at', 'last_success_at']
    readonly_fields = fields
    list_select_related = ['user']


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(RestrictedReadOnlyAdmin):
    list_display = ['id', 'user_id', 'enabled', 'enabled_since', 'updated_at']
    fields = ['id', 'user', 'enabled', 'enabled_since', 'created_at', 'updated_at']
    readonly_fields = fields
    list_select_related = ['user']
