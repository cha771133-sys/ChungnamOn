import type { AxiosRequestConfig } from 'axios'
import { api } from './api'

export type DocumentStatus = 'PENDING' | 'PROCESSING' | 'DONE' | 'FAILED'
export type DocumentStep = 'QUEUED' | 'OCR' | 'MASKING' | 'INTERPRETING' | 'VERIFYING' | 'DONE'

/** GET /api/documents/<id>/status/ 응답 */
export type StatusResponse = {
  documentId: number
  status: DocumentStatus
  step: DocumentStep
  failReason?: string
}

/** GET /api/documents/<id>/result/ 응답 */
export type ResultResponse = {
  documentId: number
  docType: string
  title: string
  originalText: string
  translatedText: string
  summary: string[]
  terms: { word: string; explain: string }[]
  deadlines: { label: string; date: string }[]
  requiredDocs: string[]
  agency: { name?: string; phone?: string; source?: string } | null
  verification: {
    status: 'MATCHED' | 'MISMATCH' | 'UNVERIFIED'
    sources: { title: string; url: string; checkedAt: string; sourceDocumentId?: number }[]
  } | null
  maskedCount: number
  lowConfidence: boolean
}

/** 업로드는 큰 파일·느린 모바일 회선을 고려해 일반 요청보다 길게 기다린다. */
const UPLOAD_TIMEOUT = 120000

/**
 * 문서 파일을 올리고 처리를 시작한다. 결과 대신 문서 ID를 먼저 받는다.
 * session을 넘기면 그 로그인 세션이 아직 유효할 때만 보낸다 (다른 계정 소유로 저장되는 것 방지).
 */
export async function uploadDocument(
  file: File,
  session?: number,
): Promise<{ documentId: number; status: DocumentStatus }> {
  const form = new FormData()
  form.append('file', file)
  const config = { timeout: UPLOAD_TIMEOUT, _session: session } as AxiosRequestConfig
  const { data } = await api.post('/api/documents/', form, config)
  return data
}

/** 문서 처리 상태와 진행 단계를 조회한다. */
export async function fetchStatus(documentId: number): Promise<StatusResponse> {
  const { data } = await api.get<StatusResponse>(`/api/documents/${documentId}/status/`)
  return data
}

/** 처리가 끝난 문서의 해석 결과를 조회한다. */
export async function fetchResult(documentId: number): Promise<ResultResponse> {
  const { data } = await api.get<ResultResponse>(`/api/documents/${documentId}/result/`)
  return data
}

/** GET /api/documents/ 목록 항목 */
export type DocumentListItem = {
  documentId: number
  title: string
  docType: string
  fileName: string
  fileType: string
  status: DocumentStatus
  step: DocumentStep
  lowConfidence: boolean
  createdAt: string
  expiresAt: string
}

/** GET /api/documents/<id>/ 상세 */
export type DocumentDetail = DocumentListItem & {
  failReason: string
  maskedCount: number
  hasResult: boolean
}

/** 페이지 단위 목록 응답 (최신순, 20개씩) */
export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] }

/** 내 문서 목록을 한 페이지 불러온다. status를 주면 그 상태만 걸러 받는다. */
export async function listDocuments(page = 1, status?: DocumentStatus): Promise<Page<DocumentListItem>> {
  const params: Record<string, string | number> = { page }
  if (status) params.status = status
  const { data } = await api.get<Page<DocumentListItem>>('/api/documents/', { params })
  return data
}

/** 내 문서 하나의 상세(실패 사유, 결과 유무 등)를 불러온다. */
export async function getDocument(documentId: number): Promise<DocumentDetail> {
  const { data } = await api.get<DocumentDetail>(`/api/documents/${documentId}/`)
  return data
}

/** 문서를 삭제한다. 해석 결과·챗봇 대화·원본 파일·일정이 함께 지워진다. 처리 중이면 409. */
export async function deleteDocument(documentId: number): Promise<void> {
  await api.delete(`/api/documents/${documentId}/`)
}
