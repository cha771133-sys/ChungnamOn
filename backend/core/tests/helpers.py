from django.contrib.auth import get_user_model


def create_user(name):
    """P0-1 통합 전후의 사용자 모델로 공통 테스트를 실행한다."""
    User = get_user_model()
    return User.objects.create_user(**{User.USERNAME_FIELD: f'{name}@example.com'})
