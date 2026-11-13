// ─────────────────────────────────────────────
// 서버 해석 결과(GET /api/documents/<id>/result/)를
// 화면이 쓰는 문서 모양(프로토타입 목업과 같은 형태)으로 바꾼다.
// 서버 형식이 바뀌면 이 파일만 고치면 된다.
// ─────────────────────────────────────────────

/** 앱 안에서 쓰는 문서 ID. 목업 문서(d1 등)와 겹치지 않게 서버 ID 앞에 s를 붙인다. */
export function localDocId(serverId) {
  return `s${serverId}`
}

/** 앱 문서 ID에서 서버 문서 ID를 꺼낸다. 서버 문서가 아니면 null. */
export function serverDocId(localId) {
  const match = /^s(\d+)$/.exec(String(localId))
  return match ? Number(match[1]) : null
}

/** 사용자 기기 기준 오늘 날짜(YYYY-MM-DD) */
export function localToday() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 출처 URL에서 기관을 짐작할 수 있도록 도메인만 꺼낸다. */
function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '') || '공식 자료'
  } catch {
    return '공식 자료'
  }
}

const VERIFY_REASON = {
  MATCHED: '문서 내용과 공식 자료가 같아요.',
  UNVERIFIED: '관련 공식 자료를 찾지 못했어요. 담당기관에 직접 확인하세요.',
  MISMATCH: '문서 내용과 공식 자료가 서로 달라요. 어느 쪽이 맞는지 단정할 수 없어요.',
}

const VERIFY_STATUSES = ['MATCHED', 'MISMATCH', 'UNVERIFIED']

/** 대조 항목 key → 화면 항목 이름(한국어 원문, 화면에서 T()로 번역). 모르는 key는 서버 label을 쓴다 */
const VERIFY_LABEL = {
  AMOUNT: '납부 금액',
  DEADLINE: '납부 기한',
  PAYMENT_METHOD: '납부 방법',
  AGENCY_CONTACT: '문의 연락처',
}

/** 알 수 없는 검증 상태는 근거 없이 단정하지 않도록 미확인으로 본다 */
function verifyStatus(value) {
  return VERIFY_STATUSES.includes(value) ? value : 'UNVERIFIED'
}

/**
 * 서버 검증 결과를 화면의 대조 항목과 근거 자료로 바꾼다.
 * - C1 형식(overall·items·sources): 항목별로 보여주고, 항목의 sourceIds로 근거 자료를 찾는다
 * - 예전 형식(status·sources만): 문서 전체 한 줄로 보여준다
 * 근거 자료가 하나도 없는 일치·불일치 항목은 근거를 보여줄 수 없으므로 미확인으로 낮춘다.
 */
export function adaptVerification(verification, docKey, docTitle) {
  const v = verification ?? {}
  const sourceMap = {}
  const keyOf = {}
  ;(Array.isArray(v.sources) ? v.sources : []).forEach((s, i) => {
    if (!s || typeof s !== 'object') return
    const key = `${docKey}-src${i}`
    if (s.id != null) keyOf[String(s.id)] = key
    sourceMap[key] = {
      agency: hostOf(s.url),
      title: s.title || '공식 자료',
      url: s.url,
      checkedAt: String(s.checkedAt ?? '').slice(0, 10),
      publishedAt: s.publishedAt ? String(s.publishedAt).slice(0, 10) : null,
    }
  })
  const items = Array.isArray(v.items) ? v.items.filter((it) => it && typeof it === 'object') : []
  if (!items.length) {
    const status = verifyStatus(v.overall ?? v.status)
    const sources = Object.keys(sourceMap)
    const shown = status !== 'UNVERIFIED' && !sources.length ? 'UNVERIFIED' : status
    return {
      sourceMap,
      verification: [{ id: 'overall', label: '문서 내용 전체', docValue: docTitle || '문서 내용', status: shown, reason: VERIFY_REASON[shown], genericReason: VERIFY_REASON[shown], sources }],
    }
  }
  const verificationItems = items.map((it, i) => {
    const sources = (Array.isArray(it.sourceIds) ? it.sourceIds : []).map((sid) => keyOf[String(sid)]).filter(Boolean)
    const raw = verifyStatus(it.status)
    const status = raw !== 'UNVERIFIED' && !sources.length ? 'UNVERIFIED' : raw
    // 서버 설명은 대조 결과와 맞을 때만 쓴다 (상태를 낮췄으면 일반 안내로 바꾼다)
    const serverReason = status === raw && typeof it.reason === 'string' && it.reason.trim() ? it.reason.trim() : ''
    return {
      id: it.key ? String(it.key).toLowerCase() : `item${i}`,
      key: it.key ?? null,
      label: VERIFY_LABEL[it.key] ?? it.label ?? '확인 항목',
      docValue: it.docValue ?? '',
      officialValue: it.officialValue ?? '',
      status,
      reason: serverReason || VERIFY_REASON[status],
      // 상태별 기본 안내 (번역 사전에 있어 모든 언어로 보여줄 수 있다)
      genericReason: VERIFY_REASON[status],
      // 서버가 한국어로 쓴 설명이라 다른 언어 화면에서는 원문 설명으로 따로 보여준다
      serverReason: Boolean(serverReason),
      sources,
    }
  })
  return { sourceMap, verification: verificationItems }
}

/**
 * 서버 결과를 화면용 문서로 바꾼다.
 * @param {object} raw 서버 해석 결과
 * @param {{ language: string, uploadedAt: string, fileName?: string, ownerId: number }} meta
 *   language: 해석을 요청한 사용자 프로필 언어 (서버가 이 언어로 번역·요약한다)
 *   ownerId: 문서를 올린 계정 ID. 같은 브라우저의 다른 계정에 보이지 않게 하는 데 쓴다
 */
export function adaptResult(raw, meta) {
  const lang = meta.language
  const id = localDocId(raw.documentId)
  const { sourceMap, verification } = adaptVerification(raw.verification, id, raw.title)
  // 문의 연락처가 공식 자료와 다르면 담당기관 화면에서 두 번호를 나란히 보여준다
  const contact = verification.find((it) => it.key === 'AGENCY_CONTACT')
  const summary = raw.summary ?? []
  const agency = raw.agency ?? {}

  return {
    id,
    serverId: raw.documentId,
    ownerId: meta.ownerId,
    hasDetail: true,
    source: 'api',
    contentLang: lang,
    docType: raw.docType || '문서',
    title: raw.title || meta.fileName || '행정문서',
    status: 'done',
    uploadedAt: meta.uploadedAt,
    fileName: meta.fileName,
    maskedCount: raw.maskedCount ?? 0,
    lowConfidence: Boolean(raw.lowConfidence),
    sender: { name: agency.name || '확인되지 않음' },
    deadline: nearestDeadline(raw.deadlines),
    deadlines: raw.deadlines ?? [],
    task: { [lang]: summary[0] ?? '' },
    requiredDocs: raw.requiredDocs ?? [],
    amount: typeof raw.amount === 'string' && raw.amount.trim() ? raw.amount.trim() : null,
    paymentMethods: Array.isArray(raw.paymentMethods) ? raw.paymentMethods.filter((m) => typeof m === 'string' && m.trim()) : [],
    summary: { [lang]: summary },
    translated: { [lang]: raw.translatedText ?? '' },
    original: raw.originalText ?? '',
    // 단어가 비어 있는 용어는 화면에서 쓸 수 없으므로 뺀다
    terms: (raw.terms ?? []).filter((t) => typeof t?.word === 'string' && t.word.trim()).map((t) => ({ word: t.word.trim(), explain: { [lang]: t.explain ?? '' } })),
    verification,
    sourceMap,
    // 체크리스트는 백엔드 체크리스트 API(A6) 연결 전까지 비워 둔다
    checklist: [],
    agency: {
      name: agency.name || '확인되지 않음',
      phone: agency.phone || '',
      // 연락처가 공식 자료와 일치하면 그 자료를 출처로 보여준다
      sourceId: contact?.status === 'MATCHED' ? (contact.sources[0] ?? null) : null,
      conflict: contact?.status === 'MISMATCH' && /\d{2,}/.test(contact.officialValue) && contact.sources.length ? { phone: contact.officialValue, sourceId: contact.sources[0] } : null,
    },
    // 서버가 전화·방문용 한국어 문장을 주면 쓰고, 없으면 담당기관 화면이 문서 정보로 만든다
    callScript: typeof raw.callScript === 'string' && raw.callScript.trim() ? raw.callScript.trim() : null,
    suggestedQuestions: [],
  }
}

/** YYYY-MM-DD 형식이면서 실제로 있는 날짜인지 (2026-13-01, 2026-02-30 등은 제외) */
export function isCalendarDate(value) {
  if (typeof value !== 'string') return false
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const date = new Date(y, mo - 1, d)
  return date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d
}

/**
 * 대표 마감일: 서버가 준 순서와 상관없이 오늘 이후 가장 가까운 날짜, 모두 지났으면 가장 최근에 지난 날짜.
 */
export function nearestDeadline(deadlines) {
  const dates = (deadlines ?? [])
    .map((d) => d?.date)
    .filter(isCalendarDate)
    .sort()
  if (!dates.length) return null
  const today = localToday()
  return dates.find((d) => d >= today) ?? dates[dates.length - 1]
}

/** 서버 상태를 화면 상태로 바꾼다. */
function screenStatus(status) {
  if (status === 'DONE') return 'done'
  if (status === 'FAILED') return 'failed'
  return 'processing'
}

/** ISO 시각을 사용자 기기 기준 날짜(YYYY-MM-DD)로 바꾼다. */
function toLocalDate(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return localToday()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * 서버 문서 목록 한 페이지를 로컬 문서 목록에 합친다.
 * - 이미 해석 결과를 받아 둔 완료 문서는 그대로 두고 제목·상태만 맞춘다.
 * - 처음 보는 문서는 목록 정보만 담은 문서로 추가하고, 열 때 결과를 불러온다(hasDetail: false).
 * - complete가 true(전체 목록을 다 받음)이면 서버에 없는 이 계정의 서버 문서를 지운다.
 *   knownIds: 앞 페이지까지 받은 문서 ID (여러 페이지를 다 받은 뒤 정리할 때 함께 넘긴다)
 */
export function mergeServerList(docs, items, { ownerId, complete, knownIds = [] }) {
  const byId = new Map(docs.map((d) => [d.id, d]))
  // 앞 페이지에서 받은 문서도 서버에 있는 것으로 본다
  const serverIds = new Set(knownIds)
  for (const item of items) {
    const id = localDocId(item.documentId)
    serverIds.add(id)
    const prev = byId.get(id)
    const status = screenStatus(item.status)
    const base = {
      id,
      serverId: item.documentId,
      ownerId,
      source: 'api',
      docType: item.docType || prev?.docType || '문서',
      title: item.title || item.fileName || prev?.title || '행정문서',
      fileName: item.fileName,
      status,
      createdAt: item.createdAt,
      uploadedAt: toLocalDate(item.createdAt),
      lowConfidence: item.lowConfidence,
    }
    // 결과를 이미 받아 둔 완료 문서는 결과 내용을 유지한다
    const keepResult = prev?.hasDetail !== false && prev?.summary && status === 'done'
    byId.set(id, keepResult ? { ...prev, ...base } : { ...prev, ...base, hasDetail: false })
  }
  let merged = [...byId.values()]
  if (complete) {
    merged = merged.filter((d) => d.source !== 'api' || d.ownerId !== ownerId || serverIds.has(d.id))
  }
  return merged
}
