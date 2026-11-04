from django.contrib import admin

from .models import CrawlLog, SourceChunk, SourceDocument


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


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'agency', 'region', 'category', 'status', 'collected_at')
    list_filter = ('agency', 'region', 'category', 'status')
    search_fields = ('title', 'source_url', 'agency')
    readonly_fields = ('id', 'collected_at', 'content_hash', 'content_text')
    inlines = (SourceChunkInline,)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, 'source_url')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SourceChunk)
class SourceChunkAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'source_document', 'chunk_order')
    list_filter = (('source_document', admin.RelatedOnlyFieldListFilter),)
    search_fields = ('chunk_text',)
    readonly_fields = ('id', 'source_document', 'chunk_order', 'chunk_text', 'embedding')
    list_select_related = ('source_document',)


@admin.register(CrawlLog)
class CrawlLogAdmin(PipelineReadOnlyMixin, admin.ModelAdmin):
    list_display = ('id', 'source_url', 'started_at', 'finished_at', 'result')
    list_filter = ('result',)
    search_fields = ('source_url',)
    readonly_fields = ('id', 'source_url', 'started_at', 'finished_at', 'result', 'error_message')
