import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { api } from './api'
import type { Page } from './documents'

/** 답변 근거 상태. GROUNDED: 공식 근거 있음 / UNVERIFIED: 미확인 / MISMATCH: 문서와 공식 자료가 다름 */
export type ChatStatus = 'GROUNDED' | 'UNVERIFIED' | 'MISMATCH'

/** 답변에 쓰인 공식 자료 (답변 당시 스냅샷) */
export type ChatSource = {
  sourceDocumentId: number
  title: string
  url: string
  checkedAt: string
  contentHash: string
}

/** /api/documents/<id>/chat/ 질문·답변 한 쌍 */
export type ChatItem = {
  chatId: number
  documentId: number
  question: string
  answer: string
  language: string
  grounded: boolean
  status: ChatStatus
  sources: ChatSource[]
  suggestedQuestions: string[]
  createdAt: string
}

/**
 * AI 답변(RAG)은 최대 120초까지 걸릴 수 있어 질문 요청만 기본(15초)보다 길게 기다린다.
 * 서버 제한 시간보다 조금 길게 잡아, 서버가 보낸 503을 프론트 시간 초과보다 먼저 받게 한다.
 */
export const CHAT_TIMEOUT = 130000

/** 대화 이력 한 번에 받는 개수 (서버 최대 100) */
export const CHAT_PAGE_SIZE = 50

/** 질문 요청이 시간 초과로 끝났는지. 서버에서는 답변이 저장됐을 수 있다. */
export function isChatTimeout(error: unknown): boolean {
  return axios.isAxiosError(error) && error.code === 'ECONNABORTED'
}

/** 문서의 대화 이력을 최신순으로 한 페이지 불러온다. */
export async function fetchChatHistory(documentId: number, page = 1): Promise<Page<ChatItem>> {
  const { data } = await api.get<Page<ChatItem>>(`/api/documents/${documentId}/chat/`, {
    params: { page, page_size: CHAT_PAGE_SIZE },
  })
  return data
}

/**
 * 문서에 질문을 보내고 답변을 받는다.
 * session을 넘기면 그 로그인 세션이 아직 유효할 때만 보낸다 (계정이 바뀐 뒤 다른 계정으로 저장되는 것 방지).
 */
export async function askQuestion(documentId: number, message: string, session?: number): Promise<ChatItem> {
  const config = { timeout: CHAT_TIMEOUT, _session: session } as AxiosRequestConfig
  const { data } = await api.post<ChatItem>(`/api/documents/${documentId}/chat/`, { message }, config)
  return data
}
