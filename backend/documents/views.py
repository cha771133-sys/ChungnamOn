from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document, InterpretationResult
from .serializers import DocumentResultSerializer, DocumentStatusSerializer, DocumentUploadSerializer
from .services.runner import start_processing


class DocumentUploadView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = DocumentUploadSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save(owner=request.user, step=Document.Step.QUEUED)
        # 저장이 커밋된 뒤에 시작해야 백그라운드 스레드가 문서를 읽을 수 있다.
        transaction.on_commit(lambda: start_processing(document.id))
        return Response(
            {'documentId': document.id, 'status': document.status},
            status=status.HTTP_201_CREATED,
        )


class DocumentStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentStatusSerializer

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)


class DocumentResultView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        document = get_object_or_404(Document, pk=pk, owner=request.user)
        if document.status != Document.Status.DONE:
            return Response(self._not_ready_body(document), status=status.HTTP_409_CONFLICT)

        result = get_object_or_404(InterpretationResult.objects.select_related('document'), document=document)
        return Response(DocumentResultSerializer(result).data)

    def _not_ready_body(self, document):
        if document.status == Document.Status.FAILED:
            return {
                'detail': '문서 처리에 실패했습니다.',
                'status': document.status,
                'step': document.step,
                'failReason': document.fail_reason,
            }
        return {'detail': '아직 처리 중입니다.', 'status': document.status, 'step': document.step}
