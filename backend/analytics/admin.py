from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path

from accounts.models import UserProfile

from .models import KPIEvent
from .services.kpi_service import get_kpi_summary


class SummaryForm(forms.Form):
    days = forms.TypedChoiceField(label='집계 기간', choices=[(7, '최근 7일'), (30, '최근 30일')], coerce=int, initial=7)
    language = forms.ChoiceField(label='분석 시작/답변 당시 언어', required=False, choices=[
        ('', '전체'), ('unknown', '언어 미확인'), *UserProfile.Language.choices,
    ])


@admin.register(KPIEvent)
class KPIEventAdmin(admin.ModelAdmin):
    change_list_template = 'admin/analytics/event_list.html'
    list_display = ('id', 'event_type', 'occurred_at', 'entity_type', 'entity_id', 'language', 'data_kind')
    list_filter = ('event_type', 'data_kind', 'language', 'occurred_at')
    date_hierarchy = 'occurred_at'
    search_fields = ('=entity_id',)
    ordering = ('-occurred_at', '-id')
    list_per_page = 50
    actions = None
    fields = ('id', 'event_type', 'occurred_at', 'created_at', 'document_id', 'entity_type', 'entity_id',
              'language', 'data_kind', 'properties')
    readonly_fields = fields

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff and request.user.has_perm('analytics.view_kpievent')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path('summary/', self.admin_site.admin_view(self.summary), name='analytics_kpi_summary'), *super().get_urls()]

    def summary(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        form = SummaryForm(request.GET or {'days': 7})
        summary = None
        if form.is_valid():
            language = form.cleaned_data['language']
            summary = get_kpi_summary(days=form.cleaned_data['days'], language='' if language == 'unknown' else language or None)
        return TemplateResponse(request, 'admin/analytics/dashboard.html', {
            **self.admin_site.each_context(request), 'title': 'KPI 요약', 'opts': self.model._meta,
            'form': form, 'summary': summary,
        }, status=200 if summary is not None else 400)
