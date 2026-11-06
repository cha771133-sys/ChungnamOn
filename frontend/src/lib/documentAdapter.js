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
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return '공식 자료'
  }
}

const VERIFY_REASON = {
  UNVERIFIED: '관련 공식 자료를 찾지 못했어요. 담당기관에 직접 확인하세요.',
  MISMATCH: '문서 내용과 공식 자료가 서로 달라요. 어느 쪽이 맞는지 단정할 수 없어요.',
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
  const verification = raw.verification ?? { status: 'UNVERIFIED', sources: [] }
  const sourceMap = {}
  const sourceIds = (verification.sources ?? []).map((s, i) => {
    const key = `${id}-src${i}`
    sourceMap[key] = {
      agency: hostOf(s.url),
      title: s.title,
      url: s.url,
      checkedAt: String(s.checkedAt ?? '').slice(0, 10),
      publishedAt: null,
    }
    return key
  })
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
    deadline: raw.deadlines?.[0]?.date ?? null,
    deadlines: raw.deadlines ?? [],
    task: { [lang]: summary[0] ?? '' },
    requiredDocs: raw.requiredDocs ?? [],
    summary: { [lang]: summary },
    translated: { [lang]: raw.translatedText ?? '' },
    original: raw.originalText ?? '',
    // 단어가 비어 있는 용어는 화면에서 쓸 수 없으므로 뺀다
    terms: (raw.terms ?? [])
      .filter((t) => typeof t?.word === 'string' && t.word.trim())
      .map((t) => ({ word: t.word.trim(), explain: { [lang]: t.explain ?? '' } })),
    // 서버는 문서 전체에 대한 대조 결과 하나를 준다 (항목별 대조는 백엔드 A5 작업 후 확장)
    verification: [
      {
        id: 'overall',
        label: '문서 내용 전체',
        docValue: raw.title || '문서 내용',
        status: verification.status,
        reason: VERIFY_REASON[verification.status],
        sources: sourceIds,
      },
    ],
    sourceMap,
    // 체크리스트는 백엔드 체크리스트 API(A6) 연결 전까지 비워 둔다
    checklist: [],
    agency: { name: agency.name || '확인되지 않음', phone: agency.phone || '', sourceId: null },
    callScript: null,
    suggestedQuestions: [],
  }
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
