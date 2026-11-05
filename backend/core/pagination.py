from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """목록 뷰는 소유자 필터 후 고유 키를 포함한 정렬을 지정해야 한다."""

    page_size = 20
    page_query_param = 'page'
    page_size_query_param = 'page_size'
    max_page_size = 100
