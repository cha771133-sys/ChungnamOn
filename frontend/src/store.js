import { createContext, useContext } from 'react'
import { SOURCES } from './data/mock.js'
import { tokenStore } from './lib/api'

export const Ctx = createContext(null)
export const useApp = () => useContext(Ctx)

export const CONTENT_LANGS = ['vi', 'en', 'ko']

const today = new Date()
today.setHours(0, 0, 0, 0)
export const TODAY = today

export function dday(s) {
  const d = new Date(s + 'T00:00:00')
  return Math.round((d - TODAY) / 86400000)
}
export function ddayLabel(n) {
  if (n === 0) return 'D-day'
  return n > 0 ? `D-${n}` : `${-n}일 지남`
}
export function fmtDate(s) {
  const [y, m, d] = s.split('-')
  return `${y}년 ${+m}월 ${+d}일`
}
export function fmtShort(s) {
  const [, m, d] = s.split('-')
  return `${+m}월 ${+d}일`
}
export function addDays(s, n) {
  const d = new Date(s + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}
export function pick(obj, lang) {
  if (!obj) return ''
  return obj[lang] ?? obj.en ?? obj.ko
}
/** 문서 상태에 맞는 화면으로 연다. 서버 문서가 분석 중이면 분석 화면에서 이어서 확인한다. */
export function openDoc(doc, nav, showToast) {
  // 목록으로만 받은 완료 문서는 결과를 먼저 불러온다
  if (doc.status === 'done' && doc.source === 'api' && doc.hasDetail === false) nav('analyzing', { docId: doc.id, reopen: true })
  else if (doc.status === 'done') nav('result', { docId: doc.id })
  else if (doc.status === 'failed') nav('failed', { docId: doc.id })
  else if (doc.serverId) nav('analyzing', { docId: doc.id })
  else showToast('아직 분석 중이에요. 잠시 후 다시 확인하세요.')
}

/** 서버에서 받은 문서인지 (목업 문서는 d1처럼 s로 시작하지 않는다) */
function isServerDocId(id) {
  return /^s\d+$/.test(String(id))
}

/**
 * 서버 문서와 그에 딸린 일정·대화를 로컬 저장소에서 지운다.
 * 로그아웃·세션 만료·다른 계정 로그인 때 호출해서, 같은 브라우저의 다음 사용자가 이전 계정의 결과를 보지 못하게 한다.
 */
export function purgeServerData(d) {
  return {
    docs: d.docs.filter((doc) => doc.source !== 'api'),
    schedules: d.schedules.filter((s) => !isServerDocId(s.docId)),
    chats: Object.fromEntries(Object.entries(d.chats ?? {}).filter(([docId]) => !isServerDocId(docId))),
  }
}

/** 현재 로그인한 계정에 보여줘도 되는 문서인지. 서버 문서는 올린 계정에만 보인다. */
export function isVisibleDoc(doc, user) {
  if (doc.source !== 'api') return true
  // 다른 탭에서 계정이 바뀐 직후에는 저장된 사용자 정보가 아직 이전 계정일 수 있으므로,
  // 사용자 정보를 저장할 때의 세션 표식이 지금 세션과 같을 때만 서버 문서를 보여준다
  return user?.id != null && doc.ownerId === user.id && user.session === tokenStore.marker()
}

/** 현재 계정에 보여줘도 되는 일정인지 (서버 일정은 문서와 같은 기준) */
export function isVisibleSchedule(schedule, user) {
  if (schedule.source !== 'api') return true
  return user?.id != null && schedule.ownerId === user.id && user.session === tokenStore.marker()
}

/** 일정까지 남은 일수. 서버 일정은 받은 날에는 서버 값(한국 날짜 기준)을, 날이 바뀌면 기기 날짜로 계산한다. */
export function scheduleDaysLeft(schedule) {
  if (schedule.source === 'api' && typeof schedule.daysLeft === 'number' && schedule.fetchedOn === todayString()) {
    return schedule.daysLeft
  }
  return dday(schedule.date)
}

/** 기기 기준 오늘(YYYY-MM-DD) */
function todayString() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 서버 문서를 최신순으로, 그 뒤에 시연용 목업 문서를 붙여 정렬한다. */
export function sortDocs(docs) {
  const api = docs.filter((d) => d.source === 'api')
  const mock = docs.filter((d) => d.source !== 'api')
  const key = (d) => d.createdAt || d.uploadedAt || ''
  return [...api.sort((a, b) => key(b).localeCompare(key(a))), ...mock]
}

/** 문서 하나와 그 문서에 딸린 일정·대화를 로컬에서 지운다. */
export function removeDocLocally(d, docId) {
  const chats = { ...(d.chats ?? {}) }
  delete chats[docId]
  return {
    docs: d.docs.filter((doc) => doc.id !== docId),
    schedules: d.schedules.filter((s) => s.docId !== docId),
    chats,
  }
}

/** 근거 자료를 찾는다. 서버 문서는 문서마다 받은 출처(sourceMap), 목업 문서는 공통 목업 출처를 쓴다. */
export function getSource(doc, id) {
  return doc?.sourceMap?.[id] ?? SOURCES[id]
}
export function needsCheck(doc) {
  return doc.status === 'failed' || (doc.verification || []).some((v) => v.status !== 'MATCHED')
}
