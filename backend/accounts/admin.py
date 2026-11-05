from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from chatbot.models import ChatMessage

from .models import User, UserProfile


class AccountCreationForm(BaseUserCreationForm):
    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ('email',)

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data['email'])


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data['email'])


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = AccountCreationForm
    form = AccountChangeForm
    ordering = ('email',)
    list_display = ('email', 'is_active', 'is_staff')
    search_fields = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('개인 정보', {'fields': ('first_name', 'last_name')}),
        ('권한', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('일시', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2')}),
    )

    def get_deleted_objects(self, objs, request):
        """부모 삭제 권한은 유지하고 ChatMessage의 CASCADE에만 직접 삭제 금지를 적용하지 않는다."""
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        permissions.discard(ChatMessage._meta.verbose_name)
        return deleted, counts, permissions, protected


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'language', 'nationality', 'visa_type')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')
