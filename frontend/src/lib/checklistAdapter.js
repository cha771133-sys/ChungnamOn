// 서버 체크리스트(/api/documents/<id>/checklist/)를 화면 체크리스트 항목으로 바꾼다.

/** 서버 상태 이름 → 화면 배지 상태. 백엔드는 CHECK_NEEDED, 화면은 NEEDS_CHECK를 쓴다 (둘 다 받는다) */
const STATE = { CONFIRMED: 'CONFIRMED', CONDITIONAL: 'CONDITIONAL', CHECK_NEEDED: 'NEEDS_CHECK', NEEDS_CHECK: 'NEEDS_CHECK' }

/** 서버 체크리스트 항목의 화면 ID */
export const localChecklistId = (itemId) => `k${itemId}`

const text = (v) => (typeof v === 'string' ? v.trim() : '')

/**
 * 서버 항목 하나를 화면 항목으로 바꾼다.
 * 알 수 없는 상태는 근거 없이 확정으로 보이지 않도록 '확인 필요'로 본다.
 * @param {object} item 서버 항목
 * @param {Record<string, string>} sourceKeyOf 서버 출처 id → 문서 출처 키 (결과 변환 때 저장)
 */
export function adaptChecklistItem(item, sourceKeyOf = {}) {
  const sources = (Array.isArray(item.sourceIds) ? item.sourceIds : [])
    .map((sid) => String(sid))
    .filter((sid) => Object.hasOwn(sourceKeyOf, sid))
    .map((sid) => sourceKeyOf[sid])
  return {
    id: localChecklistId(item.id),
    serverItemId: item.id,
    text: text(item.text) || '할 일',
    detail: text(item.detail),
    docs: Array.isArray(item.docs) ? item.docs.map(text).filter(Boolean) : [],
    method: text(item.method),
    due: typeof item.due === 'string' && item.due ? item.due.slice(0, 10) : null,
    state: Object.hasOwn(STATE, item.state) ? STATE[item.state] : 'NEEDS_CHECK',
    done: Boolean(item.done),
    note: text(item.note),
    sources,
  }
}

/** 서버 체크리스트 응답을 화면 항목 목록으로 바꾼다. */
export function adaptChecklist(raw, sourceKeyOf) {
  return (Array.isArray(raw?.items) ? raw.items : []).filter((it) => it && it.id != null).map((it) => adaptChecklistItem(it, sourceKeyOf))
}
