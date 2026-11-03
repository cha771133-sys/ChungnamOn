from django.core.exceptions import ObjectDoesNotExist
from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    """인증된 객체 소유자만 허용한다. 목록 필터와 생성 시 소유자 지정은 뷰의 책임이다."""

    message = '이 데이터에 접근할 권한이 없습니다.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view) or request.user.pk is None:
            return False
        try:
            owner = getattr(obj, 'owner', None)
        except ObjectDoesNotExist:
            return False
        return owner is not None and owner == request.user
