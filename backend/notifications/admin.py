from django.contrib import admin

from .models import NotificationPreference, PushDevice


class RestrictedReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
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
