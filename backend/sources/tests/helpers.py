from sources.models import SourceDocument


def create_document(**overrides):
    values = {
        'source_url': 'https://www.asan.go.kr/notices/1',
        'title': '주민세 납부 안내',
        'agency': '아산시청',
        'region': '아산시',
        'category': '지방세',
    }
    values.update(overrides)
    return SourceDocument.objects.create(**values)
