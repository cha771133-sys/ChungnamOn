from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken

from documents.models import Document, InterpretationResult
from chatbot.models import ChatMessage


class ChatFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('chat-owner@example.com')
        cls.other = get_user_model().objects.create_user('chat-other@example.com')
        cls.document = cls.make_document(cls.user)
        cls.other_document = cls.make_document(cls.other)
        cls.result = InterpretationResult.objects.create(
            document=cls.document, original_text='주소: [주소 가림]',
            translated_text='Masked translation', summary=['요약'], terms=[],
            deadlines=[{'label': '확인 필요', 'date': '2026-12-01'}],
            required_docs=['문서'], agency={'name': '테스트 기관'}, language='ko',
            verification={'status': 'MATCHED', 'sources': [{
                'title': '예시 (더미)', 'url': 'https://www.asan.go.kr/', 'checkedAt': '2026-10-30',
            }]},
        )

    @staticmethod
    def make_document(owner, **overrides):
        return Document.objects.create(**{
            'owner': owner, 'file': 'documents/test.pdf', 'file_name': 'test.pdf',
            'file_type': 'PDF', 'status': Document.Status.DONE, **overrides,
        })

    @staticmethod
    def make_message(document, **overrides):
        return ChatMessage.objects.create(**{
            'document': document, 'question': '질문', 'answer': '공식 근거 확인 필요', 'language': 'ko', **overrides,
        })

    @property
    def url(self):
        return f'/api/documents/{self.document.pk}/chat/'

    def authenticate(self, user=None):
        """지정 사용자 또는 기본 소유자의 실제 JWT로 테스트 클라이언트를 인증한다."""
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(user or self.user)))
