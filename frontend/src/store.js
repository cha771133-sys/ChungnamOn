import { createContext, useContext } from 'react'
import { SOURCES } from './data/mock.js'

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
  if (doc.status === 'done') nav('result', { docId: doc.id })
  else if (doc.status === 'failed') nav('failed', { docId: doc.id })
  else if (doc.serverId) nav('analyzing', { docId: doc.id })
  else showToast('아직 분석 중이에요. 잠시 후 다시 확인하세요.')
}

/** 근거 자료를 찾는다. 서버 문서는 문서마다 받은 출처(sourceMap), 목업 문서는 공통 목업 출처를 쓴다. */
export function getSource(doc, id) {
  return doc?.sourceMap?.[id] ?? SOURCES[id]
}
export function needsCheck(doc) {
  return doc.status === 'failed' || (doc.verification || []).some((v) => v.status !== 'MATCHED')
}
