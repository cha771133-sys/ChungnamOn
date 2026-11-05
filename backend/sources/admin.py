from django import forms
from django.contrib import admin
from django.contrib import messages
from django.db import DatabaseError, transaction
from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils import timezone

from .models import Agency, AgencyAlias, CrawlLog, SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from .services.errors import CollectionError
from .services.freshness import freshness, next_due_at, filter_freshness
from .services.refresh_service import review_revision


class PipelineReadOnlyMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SourceChunkInline(PipelineReadOnlyMixin, admin.TabularInline):
    model = SourceChunk
    fields = ('id', 'chunk_order', 'chunk_text')
    readonly_fields = fields
    extra = 0
    show_change_link = True
    ordering = ('chunk_order',)


class FreshnessFilter(admin.SimpleListFilter):
    title = '자료 최신성'
    parameter_name = 'freshness'

    def lookups(self, request, model_admin):
        return [('CURRENT', '최근 확인'), ('STALE', '오래된 자료'), ('REFRESH_FAILED', '재수집 실패'), ('REVIEW_REQUIRED', '검토 필요')]

    def queryset(self, request, queryset):
        return filter_freshness(queryset, self.value()) if self.value() else queryset


class SourceDocumentAdminForm(forms.ModelForm):
    class Meta:
        model = SourceDocument
        fields = '__all__'

    def clean(self):
        """Admin POST 트랜잭션에서 승인과 직렬화하여 이전 폼의 재활성화를 막는다."""
        cleaned_data = super().clean()
        if self.instance.pk and cleaned_data.get('status') == SourceDocument.Status.ACTIVE:
            current = SourceDocument.objects.select_for_update().get(pk=self.instance.pk)
            if current.ai_reprocessing_required:
                self.add_error('status', 'AI 재처리가 끝날 때까지 자료를 사용 중으로 변경할 수 없습니다.')
        return cleaned_data


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    form = SourceDocumentAdminForm
    list_display = ('id', 'title', 'source_url', 'provider', 'external_id', 'agency', 'region', 'category', 'published_at', 'collected_at', 'last_success_at', 'updated_at', 'status', 'freshness_state', 'review_status', 'reviewed_by', 'reviewed_at')
    list_filter = ('agency', 'region', 'category', 'status', 'provider', 'review_status', FreshnessFilter, 'ai_reprocessing_required')
    search_fields = ('title', 'source_url', 'agency')
    readonly_fields = ('id', 'collected_at', 'content_hash', 'content_text', 'provider', 'external_id', 'effective_at', 'metadata', 'last_success_at', 'observed_started_at', 'observed_hash', 'updated_at', 'version', 'review_status', 'reviewed_by', 'reviewed_at', 'ai_reprocessing_required')
    ordering = ('collected_at', 'id')
    list_select_related = ('refresh_target', 'reviewed_by')
    inlines = (SourceChunkInline,)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, 'source_url')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='갱신 상태')
    def freshness_state(self, obj):
        return freshness(obj)

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        with transaction.atomic():
            # 폼을 여는 동안 승인·재수집된 필드가 이전 폼 인스턴스로 되돌아가지 않게 한다.
            fields = [field for field in form.changed_data if field in ('title', 'agency', 'region', 'category', 'status', 'published_at')]
            values = {field: getattr(obj, field) for field in fields}
            if values:
                SourceDocument.objects.filter(pk=obj.pk).update(**values, version=F('version') + 1, updated_at=timezone.now())
            obj.refresh_from_db()


@admin.register(SourceChunk)
class SourceChunkAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'source_document', 'chunk_order')
    list_filter = (('source_document', admin.RelatedOnlyFieldListFilter),)
    search_fields = ('chunk_text',)
    readonly_fields = ('id', 'source_document', 'chunk_order', 'chunk_text', 'embedding', 'source_content_hash')
    list_select_related = ('source_document',)


@admin.register(CrawlLog)
class CrawlLogAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'source_url', 'provider', 'external_id', 'target', 'started_at', 'finished_at', 'result', 'outcome', 'error_code', 'has_normal_document')
    list_filter = ('result', 'provider', 'error_code')
    search_fields = ('source_url', 'external_id')
    readonly_fields = tuple(field.name for field in CrawlLog._meta.fields)
    list_select_related = ('target__document',)

    @admin.display(description='기존 정상 자료', boolean=True)
    def has_normal_document(self, obj):
        return bool(obj.target_id and obj.target.document_id and obj.target.document.content_hash)


@admin.register(SourceRefreshTarget)
class SourceRefreshTargetAdmin(admin.ModelAdmin):
    list_display = ('id', 'provider', 'external_id', 'source_url', 'document', 'enabled', 'last_attempt_at', 'last_success_at', 'failures', 'next_check')
    list_filter = ('provider', 'enabled')
    search_fields = ('external_id', 'source_url')
    readonly_fields = ('document', 'last_attempt_at', 'last_success_at', 'failures', 'retry_after', 'lease_token', 'lease_expires_at')
    list_select_related = ('document',)

    def get_readonly_fields(self, request, obj=None):
        return (*self.readonly_fields, 'provider', 'external_id', 'source_url') if obj else self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        fields = [field for field in form.changed_data if field in ('configuration', 'enabled', 'interval_hours')]
        if fields:
            SourceRefreshTarget.objects.filter(pk=obj.pk).update(**{field: getattr(obj, field) for field in fields})
        obj.refresh_from_db()

    @admin.display(description='다음 확인 예정')
    def next_check(self, obj):
        return next_due_at(obj)


@admin.register(SourceRevision)
class SourceRevisionAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'document', 'provider', 'content_hash', 'collected_at', 'review_status', 'reviewed_by', 'reviewed_at')
    list_filter = ('review_status', 'provider')
    search_fields = ('document__title', 'document__external_id', 'content_hash')
    readonly_fields = tuple(field.name for field in SourceRevision._meta.fields)
    list_select_related = ('document', 'reviewed_by')
    actions = ('approve_candidates', 'reject_candidates')

    def has_review_permission(self, request):
        user = request.user
        return user.is_active and user.is_staff and user.has_perm('sources.change_sourcerevision') and user.has_perm('sources.change_sourcedocument')

    def _review(self, request, queryset, *, approve):
        for pk in queryset.values_list('pk', flat=True):
            try:
                revision = review_revision(pk, request.user, approve=approve)
                self.log_change(request, revision, '공식자료 변경 후보 승인' if approve else '공식자료 변경 후보 반려')
                self.message_user(request, f'후보 {pk}: 검토 결과를 기록했습니다.', messages.SUCCESS)
            except CollectionError as error:
                self.message_user(request, f'후보 {pk}: {error}', messages.ERROR)
            except (DatabaseError, ValidationError):
                self.message_user(request, f'후보 {pk}: 저장 충돌 또는 검증 실패입니다. 최신 상태를 다시 확인하세요.', messages.ERROR)

    @admin.action(description='선택한 변경 후보 승인', permissions=['review'])
    def approve_candidates(self, request, queryset):
        self._review(request, queryset, approve=True)

    @admin.action(description='선택한 변경 후보 반려', permissions=['review'])
    def reject_candidates(self, request, queryset):
        self._review(request, queryset, approve=False)


class AgencyAliasInline(admin.TabularInline):
    model = AgencyAlias
    extra = 0


@admin.register(Agency)
class AgencyAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'region', 'phone', 'phone_type', 'source_url', 'checked_at')
    list_filter = ('region', 'phone_type')
    search_fields = ('name', 'department', 'aliases__name')
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = (AgencyAliasInline,)
