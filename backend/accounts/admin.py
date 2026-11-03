from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

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


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'language', 'nationality', 'visa_type')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')
