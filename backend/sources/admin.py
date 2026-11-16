from django import forms
from django.contrib import admin
from django.contrib import messages
from django.db import DatabaseError, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import Agency, AgencyAlias, CrawlLog, SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from .services.errors import CollectionError
from .services.freshness import freshness, next_due_at, filter_freshness
from .services.manual_import import import_official_txt
from .services.refresh_service import document_snapshot, review_revision
from .services.source_registry import CATEGORIES, REGIONS
from .services.usage_conditions import is_manual_metadata, validate_manual_approval


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
        """자료 전체를 메모리에 올리지 않고 선택한 최신성 상태를 DB에서 필터링한다."""
        return filter_freshness(queryset, self.value()) if self.value() else queryset


class SourceDocumentAdminForm(forms.ModelForm):
    expected_version = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)

    class Meta:
        model = SourceDocument
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        """화면을 연 시점의 승인 정보 버전을 숨김 필드로 제출하도록 초기화한다."""
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['expected_version'] = self.instance.version

    def clean(self):
        """모든 상태의 저장을 승인과 직렬화하고 이전 화면의 메타데이터·상태 덮어쓰기를 막는다."""
        cleaned_data = super().clean()
        if self.instance.pk:
            current = SourceDocument.objects.select_for_update().get(pk=self.instance.pk)
            if cleaned_data.get('expected_version') != current.version:
                self.add_error(None, '화면을 연 뒤 자료가 변경되었습니다. 최신 화면을 다시 열어 확인하세요.')
            if cleaned_data.get('status') == SourceDocument.Status.ACTIVE and current.ai_reprocessing_required:
                self.add_error('status', 'AI 재처리가 끝날 때까지 자료를 사용 중으로 변경할 수 없습니다.')
            if cleaned_data.get('status') == SourceDocument.Status.ACTIVE and is_manual_metadata(current.metadata):
                if current.review_status != SourceDocument.ReviewStatus.APPROVED:
                    self.add_error('status', 'TXT는 변경 후보의 관리자 승인 전 사용 중으로 변경할 수 없습니다.')
                try:
                    validate_manual_approval(document_snapshot(current))
                except CollectionError as error:
                    self.add_error('status', str(error))
        return cleaned_data


class OfficialTxtImportForm(forms.Form):
    required_css_class = 'required'

    file = forms.FileField(
        label='공식자료 TXT 파일', widget=forms.ClearableFileInput(attrs={'accept': '.txt,text/plain'}),
        help_text='UTF-8 또는 UTF-8 BOM .txt, BOM 포함 최대 2 MiB. 오류가 나면 파일을 다시 선택하세요.',
    )
    title = forms.CharField(label='공식자료 제목', max_length=500, widget=forms.TextInput(attrs={'class': 'vLargeTextField'}))
    agency = forms.CharField(label='공식 기관명', max_length=255, widget=forms.TextInput(attrs={'class': 'vTextField'}))
    url = forms.URLField(label='공식 원문 HTTPS URL', max_length=2048, widget=forms.URLInput(attrs={'class': 'vURLField'}))
    region = forms.ChoiceField(label='적용 지역', choices=[(value, value) for value in sorted(REGIONS)], initial='전국')
    category = forms.ChoiceField(label='분류', choices=[(value, value) for value in sorted(CATEGORIES)])
    published_at = forms.DateField(label='공식 발행일', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    terms_status = forms.ChoiceField(
        label='AI 이용조건 확인 상태', initial='unverified',
        choices=[('unverified', '미확인'), ('confirmed', 'AI 이용 허용 확인'), ('prohibited', '가공 금지')],
    )
    terms_note = forms.CharField(
        label='이용조건 및 확인 근거', max_length=4000,
        widget=forms.TextInput(attrs={'class': 'vLargeTextField'}),
        help_text='AI 정제·청크·임베딩·검색 허용 근거 또는 미확인·금지 사유를 한 줄로 기록하세요.',
    )
    terms_url = forms.URLField(label='공식 이용조건 HTTPS URL', max_length=2048, required=False, widget=forms.URLInput(attrs={'class': 'vURLField'}))
    terms_checked_on = forms.DateField(label='이용조건 확인일', required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('terms_status') == 'confirmed' and not cleaned_data.get('terms_checked_on'):
            self.add_error('terms_checked_on', 'AI 이용 허용 확인에는 실제 확인일이 필요합니다.')
        for field in ('published_at', 'terms_checked_on'):
            value = cleaned_data.get(field)
            if value and value > timezone.localdate():
                self.add_error(field, '미래 날짜는 입력할 수 없습니다.')
        return cleaned_data


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    form = SourceDocumentAdminForm
    change_list_template = 'admin/sources/sourcedocument/change_list.html'
    list_display = ('id', 'title', 'source_url', 'provider', 'external_id', 'agency', 'region', 'category', 'published_at', 'collected_at', 'last_success_at', 'updated_at', 'status', 'freshness_state', 'review_status', 'reviewed_by', 'reviewed_at')
    list_filter = ('agency', 'region', 'category', 'status', 'provider', 'review_status', FreshnessFilter, 'ai_reprocessing_required')
    search_fields = ('title', 'source_url', 'agency')
    readonly_fields = ('id', 'collected_at', 'content_hash', 'content_text', 'provider', 'external_id', 'effective_at', 'metadata', 'last_success_at', 'observed_started_at', 'observed_hash', 'updated_at', 'version', 'review_status', 'reviewed_by', 'reviewed_at', 'ai_reprocessing_required')
    ordering = ('collected_at', 'id')
    list_select_related = ('refresh_target', 'reviewed_by')
    inlines = (SourceChunkInline,)

    def has_txt_import_permission(self, request):
        """자료·후보를 생성하고 기존 대기 후보를 갱신할 권한을 모두 요구한다."""
        user = request.user
        return user.is_active and user.is_staff and all(user.has_perm(permission) for permission in (
            'sources.add_sourcedocument', 'sources.change_sourcedocument',
            'sources.add_sourcerevision', 'sources.change_sourcerevision',
        ))

    def get_urls(self):
        return [path('import-txt/', self.admin_site.admin_view(self.import_txt_view),
                     name='sources_sourcedocument_import_txt'), *super().get_urls()]

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}), 'has_txt_import_permission': self.has_txt_import_permission(request),
        })

    @method_decorator(require_http_methods(['GET', 'POST']))
    def import_txt_view(self, request):
        """기존 수동 등록과 감사 로그를 함께 저장하고 기존 후보 검토 화면으로 연결한다."""
        if not self.has_txt_import_permission(request):
            raise PermissionDenied
        form = OfficialTxtImportForm(request.POST, request.FILES) if request.method == 'POST' else OfficialTxtImportForm()
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    result = import_official_txt(**form.cleaned_data, registered_by=request.user.get_username())
                    if result.revision_id:
                        obj = SourceRevision.objects.get(pk=result.revision_id)
                        destination = reverse('admin:sources_sourcerevision_change', args=[obj.pk], current_app=self.admin_site.name)
                    else:
                        obj = SourceDocument.objects.get(pk=result.document_id)
                        destination = reverse('admin:sources_sourcedocument_change', args=[obj.pk], current_app=self.admin_site.name)
                    audit_message = f'공식자료 TXT 등록: {result.outcome}, 문서 {result.document_id}, 본문 해시 {result.content_hash}'
                    if result.outcome in ('new', 'candidate'):
                        self.log_addition(request, obj, audit_message)
                    else:
                        self.log_change(request, obj, audit_message)
            except CollectionError as error:
                form.add_error('file' if error.code.startswith('TXT_') else None, str(error))
            except (DatabaseError, ValidationError):
                form.add_error(None, '저장 충돌 또는 검증 실패입니다. 최신 자료 상태를 확인한 뒤 파일을 다시 선택하세요.')
            else:
                if result.revision_id:
                    self.message_user(request, f'문서 {result.document_id} / 후보 {result.revision_id}: 검토 대기로 저장했습니다. 동일 본문의 대기 후보는 재사용합니다.', messages.SUCCESS)
                else:
                    self.message_user(request, f'문서 {result.document_id}: 새 후보 없이 처리했습니다. 최신 자료와 검토 상태를 확인하세요.', messages.INFO)
                return HttpResponseRedirect(destination)
        request.current_app = self.admin_site.name
        return TemplateResponse(request, 'admin/sources/sourcedocument/import_txt.html', {
            **self.admin_site.each_context(request), 'opts': self.model._meta,
            'title': '공식자료 TXT 등록', 'form': form,
        })

    def get_readonly_fields(self, request, obj=None):
        """기존 자료의 출처 식별자는 폼에서 바꾸지 못하게 하여 수집 대상과 일치시킨다."""
        if obj is not None:
            return (*self.readonly_fields, 'source_url')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='갱신 상태')
    def freshness_state(self, obj):
        """검토·실패·유형별 확인 주기를 종합한 계산 상태를 목록에 표시한다."""
        return freshness(obj)

    def save_model(self, request, obj, form, change):
        """변경한 관리 필드만 저장하고 버전을 올려 동시 수집·승인 결과를 보존한다."""
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
        """수집 실패와 별개로 대상에 기존 정상 본문이 남아 있는지 표시한다."""
        return bool(obj.target_id and obj.target.document_id and obj.target.document.content_hash)


@admin.register(SourceRefreshTarget)
class SourceRefreshTargetAdmin(admin.ModelAdmin):
    list_display = ('id', 'provider', 'external_id', 'source_url', 'document', 'enabled', 'last_attempt_at', 'last_success_at', 'failures', 'next_check')
    list_filter = ('provider', 'enabled')
    search_fields = ('external_id', 'source_url')
    readonly_fields = ('document', 'last_attempt_at', 'last_success_at', 'failures', 'retry_after', 'lease_token', 'lease_expires_at')
    list_select_related = ('document',)

    def get_readonly_fields(self, request, obj=None):
        """등록 후 식별자는 고정하고 수집 설정·주기·활성 여부만 수정한다."""
        return (*self.readonly_fields, 'provider', 'external_id', 'source_url') if obj else self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """설정 변경만 저장하여 이전 폼이 현재 실행 소유권이나 성공일을 덮어쓰지 않게 한다."""
        if not change:
            return super().save_model(request, obj, form, change)
        fields = [field for field in form.changed_data if field in ('configuration', 'enabled', 'interval_hours')]
        if fields:
            SourceRefreshTarget.objects.filter(pk=obj.pk).update(**{field: getattr(obj, field) for field in fields})
        obj.refresh_from_db()

    @admin.display(description='다음 확인 예정')
    def next_check(self, obj):
        """대상 주기 또는 실패 재시도를 반영한 다음 확인 시각을 표시한다."""
        return next_due_at(obj)


@admin.register(SourceRevision)
class SourceRevisionAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'document', 'provider', 'registration_method', 'content_hash', 'collected_at', 'review_status', 'reviewed_by', 'reviewed_at')
    list_filter = ('review_status', 'provider')
    search_fields = ('document__title', 'document__external_id', 'content_hash')
    readonly_fields = ('manual_review_notice', *tuple(field.name for field in SourceRevision._meta.fields))
    list_select_related = ('document', 'reviewed_by')
    actions = ('approve_candidates', 'reject_candidates')

    @admin.display(description='등록 방식')
    def registration_method(self, obj):
        return '수동 TXT' if is_manual_metadata(obj.metadata.get('metadata', {})) else '자동 수집'

    @admin.display(description='TXT 원문·이용조건 검토')
    def manual_review_notice(self, obj):
        if self.registration_method(obj) != '수동 TXT':
            return '해당 없음'
        return (
            '공식 URL만으로 원문 일치를 보증하지 않습니다. 새 본문과 공식 원문을 직접 대조하고 '
            'metadata의 manual_import(파일명·원본 해시·등록자), usage_terms(조건·근거·확인일)를 검토하세요. '
            'AI 정제·청크·임베딩·검색 허용과 현재 적용 범위를 확인한 뒤 기존 승인 액션을 사용하세요. '
            '이용조건 미확인·가공 금지·목록 제외 자료는 승인할 수 없습니다.'
        )

    def has_review_permission(self, request):
        """활성 staff와 자료·후보 양쪽의 수정 권한이 있는 사용자만 검토하게 한다."""
        user = request.user
        return user.is_active and user.is_staff and user.has_perm('sources.change_sourcerevision') and user.has_perm('sources.change_sourcedocument')

    def _review(self, request, queryset, *, approve):
        """후보별 충돌을 격리하며 실제 검토자·시각과 Admin 변경 이력을 남긴다."""
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
        """선택한 후보를 서비스의 최신 버전·관측 검사 후 승인한다."""
        self._review(request, queryset, approve=True)

    @admin.action(description='선택한 변경 후보 반려', permissions=['review'])
    def reject_candidates(self, request, queryset):
        """기존 승인 자료를 유지하면서 선택한 후보의 반려 이력을 기록한다."""
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
