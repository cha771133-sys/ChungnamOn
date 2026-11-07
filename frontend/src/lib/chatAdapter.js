// 서버 대화(/api/documents/<id>/chat/)를 화면이 쓰는 메시지 모양으로 바꾼다.

/** 출처 URL에서 도메인만 꺼내 기관 이름 대신 보여준다. */
function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return '공식 자료'
  }
}

/** 서버 출처를 근거 시트(SourceCard)가 쓰는 모양으로 바꾼다. */
export function adaptChatSource(s) {
  return {
    agency: hostOf(s.url),
    title: s.title,
    url: s.url,
    checkedAt: String(s.checkedAt ?? '').slice(0, 10),
    publishedAt: null,
  }
}

const STATUSES = ['GROUNDED', 'UNVERIFIED', 'MISMATCH']

/**
 * 서버 질문·답변 한 쌍을 화면 메시지 두 개(질문, 답변)로 바꾼다.
 * 근거가 확인되지 않은 상태는 모두 미확인으로 보여준다 (근거 없이 단정하지 않음).
 */
export function adaptChatItem(item) {
  const status = STATUSES.includes(item.status) ? item.status : 'UNVERIFIED'
  const sources = Array.isArray(item.sources) ? item.sources.map(adaptChatSource) : []
  // 근거 있음·불일치인데 출처가 비어 있으면 근거를 보여줄 수 없으므로 미확인으로 낮춘다
  const shown = status !== 'UNVERIFIED' && sources.length === 0 ? 'UNVERIFIED' : status
  return [
    { id: `c${item.chatId}-q`, chatId: item.chatId, role: 'user', text: item.question, createdAt: item.createdAt },
    {
      id: `c${item.chatId}-a`,
      chatId: item.chatId,
      role: 'assistant',
      text: item.answer,
      status: shown,
      grounded: shown === 'GROUNDED',
      sources,
      follow: (item.suggestedQuestions ?? []).filter((q) => typeof q === 'string' && q.trim()),
      createdAt: item.createdAt,
    },
  ]
}

/**
 * 받은 이력(최신순)을 기존 메시지(오래된 순)에 합친다. 같은 질문·답변은 한 번만 넣는다.
 * 새 질문이 생기면 서버 페이지가 밀려 같은 항목을 다시 받을 수 있으므로 chatId로 중복을 걸러낸다.
 */
export function mergeChatItems(messages, items) {
  const known = new Set(messages.map((m) => m.chatId).filter((id) => id != null))
  const added = items.filter((it) => !known.has(it.chatId)).flatMap(adaptChatItem)
  return [...messages, ...added].sort((a, b) => order(a) - order(b))
}

/** 메시지 정렬 순서: chatId 오름차순, 같은 쌍에서는 질문이 먼저 */
function order(m) {
  return (m.chatId ?? Number.MAX_SAFE_INTEGER) * 2 + (m.role === 'assistant' ? 1 : 0)
}

/** 첫 질문 추천 (서버가 문서별 추천 질문을 주기 전까지 쓰는 기본값). 보기 언어로 보여주고 그대로 보낸다. */
const STARTERS = {
  ko: ['무엇을 해야 하나요?', '마감일이 언제예요?', '어떤 서류가 필요해요?', '어디에 문의하면 되나요?'],
  vi: ['Tôi cần phải làm gì?', 'Hạn chót là khi nào?', 'Tôi cần chuẩn bị giấy tờ gì?', 'Tôi nên liên hệ ở đâu?'],
  en: ['What do I need to do?', 'When is the deadline?', 'Which documents do I need?', 'Who should I contact?'],
}

/** 문서 정보에 맞는 첫 질문 추천. 마감일·준비서류를 찾지 못한 문서에서는 그 질문을 뺀다. */
export function starterQuestions(lang, doc) {
  const [todo, deadline, papers, contact] = STARTERS[lang] ?? STARTERS.en
  const list = [todo]
  if (!doc || doc.deadline) list.push(deadline)
  if (!doc || doc.requiredDocs?.length) list.push(papers)
  list.push(contact)
  return list
}
