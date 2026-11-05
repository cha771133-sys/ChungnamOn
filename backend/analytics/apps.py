from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'analytics'
    verbose_name = 'KPI 운영 지표'

    def ready(self):
        from . import signals  # noqa: F401
