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

/** 문서 파일을 올리고 처리를 시작한다. 결과 대신 문서 ID를 먼저 받는다. */
export async function uploadDocument(file: File): Promise<{ documentId: number; status: DocumentStatus }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/api/documents/', form, { timeout: UPLOAD_TIMEOUT })
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
