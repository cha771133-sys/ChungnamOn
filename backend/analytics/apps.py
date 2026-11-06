from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'analytics'
    verbose_name = 'KPI 운영 지표'

    def ready(self):
        """앱 초기화 시 문서 저장·삭제 관찰 신호를 등록한다."""
        from . import signals  # noqa: F401
