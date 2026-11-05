class CollectionError(Exception):
    """외부 응답·URL·인증 정보를 포함하지 않는 수집 오류."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')
