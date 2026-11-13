import type { AxiosRequestConfig } from 'axios'
import { api } from './api'

/** 체크리스트 항목 상태. CONFIRMED: 확정 / CONDITIONAL: 조건부 / CHECK_NEEDED: 기관 확인 필요 */
export type ChecklistState = 'CONFIRMED' | 'CONDITIONAL' | 'CHECK_NEEDED'

/** /api/documents/<id>/checklist/ 항목 */
export type ChecklistItem = {
  id: number
  text: string
  detail: string
  docs: string[]
  method: string
  due: string | null
  state: ChecklistState
  done: boolean
  doneAt: string | null
  note: string
  /** 검증 결과 sources의 id (예: "s1") */
  sourceIds: string[]
}

export type Checklist = {
  documentId: number
  total: number
  doneCount: number
  progress: number
  items: ChecklistItem[]
}

/** 문서의 체크리스트(항목·진행률)를 불러온다. 문서를 다시 처리하면 항목 ID가 바뀐다. */
export async function getChecklist(documentId: number): Promise<Checklist> {
  const { data } = await api.get<Checklist>(`/api/documents/${documentId}/checklist/`)
  return data
}

/**
 * 체크리스트 항목의 완료 여부를 바꾼다.
 * session을 넘기면 그 로그인 세션이 아직 유효할 때만 보낸다 (계정이 바뀐 뒤 다른 계정 데이터가 바뀌는 것 방지).
 */
export async function setChecklistDone(documentId: number, itemId: number, done: boolean, session?: number): Promise<ChecklistItem> {
  const config = { _session: session } as AxiosRequestConfig
  const { data } = await api.patch<ChecklistItem>(`/api/documents/${documentId}/checklist/${itemId}/`, { done }, config)
  return data
}
