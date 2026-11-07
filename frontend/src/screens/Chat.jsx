import { useEffect, useRef, useState } from 'react'
import { Send, ShieldCheck, HelpCircle, AlertTriangle, Phone, FileText, RefreshCw, History, FileX } from 'lucide-react'
import { useApp, pick, fmtDate, isVisibleDoc, openDoc } from '../store.js'
import { CHAT_KB, CHAT_FALLBACK, SOURCES } from '../data/mock.js'
import { TopBar, Empty } from '../components/ui.jsx'
import { askQuestion, fetchChatHistory, isChatTimeout } from '../lib/chat'
import { mergeChatItems, starterQuestions } from '../lib/chatAdapter.js'
import { getDocument } from '../lib/documents'
import { serverDocId } from '../lib/documentAdapter.js'
import { parseApiError, StaleSessionError, tokenStore } from '../lib/api'
import { T } from '../i18n.js'

/** 서버 질문 최대 길이 */
const MAX_QUESTION = 2000
/** 이 시간이 지나도 답이 없으면 "공식 자료를 찾는 중" 안내를 보여준다 */
const SLOW_AFTER = 12000

/** 기기에 저장된 문서 상태(done·failed·processing)를 서버 상태 이름으로 맞춘다 */
const LOCAL_STATUS = { done: 'DONE', failed: 'FAILED', processing: 'PROCESSING' }

/** 움직임 줄이기 설정이면 글자 스트리밍 효과를 쓰지 않는다 */
function prefersReducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** 문서 챗봇 화면. 시연용 목업 문서는 기기 안 예시 답변으로, 서버 문서는 챗봇 API로 답한다. */
export default function Chat({ docId }) {
  const { data } = useApp()
  const found = data.docs.find((d) => d.id === docId)
  if (found && found.source !== 'api') return <MockChat doc={found} />
  // 이 기기에 없는 서버 문서(다른 기기에서 올린 문서 등)도 문서 ID만으로 대화할 수 있다
  if (serverDocId(docId) == null) return null
  return <ServerChat docId={docId} />
}

/* ───────────────────────── 서버 문서 ───────────────────────── */

function ServerChat({ docId }) {
  const { data, back, nav, tab, setSheet, contentLang, online, showToast } = useApp()
  const serverId = serverDocId(docId)
  const found = data.docs.find((d) => d.id === docId)
  // 다른 계정이 이 브라우저에 남긴 문서 정보는 쓰지 않는다 (대화 자체는 서버가 소유자만 허용)
  const local = found && isVisibleDoc(found, data.user) ? found : null
  const hasDetail = Boolean(local && local.hasDetail !== false && Array.isArray(local.verification))

  const [messages, setMessages] = useState([])
  const [historyState, setHistoryState] = useState('loading') // loading | ready | error
  const [nextPage, setNextPage] = useState(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [meta, setMeta] = useState(null) // 이 기기에 결과가 없는 문서의 서버 상세
  const [notFound, setNotFound] = useState(false)
  const [notReady, setNotReady] = useState(false)
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(null) // 답을 기다리는 질문
  const [slow, setSlow] = useState(false)
  const [failed, setFailed] = useState(null) // { question, text, action: 'retry' | 'reload' }
  const [reveal, setReveal] = useState(null) // { id, shown } 새 답변을 글자 단위로 보여주는 중
  const endRef = useRef(null)
  const aliveRef = useRef(true)
  // 화면에 들어온 로그인 세션. 계정이 바뀌면 이전 계정의 대화를 받지도 보내지도 않는다
  const sessionRef = useRef(tokenStore.session())
  const firstScroll = useRef(true)
  // 이전 대화를 위에 붙일 때는 아래로 스크롤하지 않는다
  const skipScroll = useRef(false)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  /** 응답을 화면에 반영해도 되는지 (화면이 살아 있고 같은 세션) */
  const fresh = () => aliveRef.current && tokenStore.session() === sessionRef.current

  /** 대화 이력 첫 페이지를 불러와 합친다. */
  const loadHistory = () => {
    setHistoryState((s) => (s === 'ready' ? s : 'loading'))
    return fetchChatHistory(serverId, 1)
      .then((res) => {
        if (!fresh()) return
        setMessages((prev) => mergeChatItems(prev, res.results))
        setNextPage((p) => p ?? (res.next ? 2 : null))
        setHistoryState('ready')
      })
      .catch((err) => {
        if (!fresh()) return
        const info = parseApiError(err)
        if (info.status === 404) setNotFound(true)
        else setHistoryState((s) => (s === 'ready' ? s : 'error'))
        throw err
      })
  }

  useEffect(() => {
    loadHistory().catch(() => {})
    // 결과를 받아 두지 않은 문서는 제목·상태를 서버에서 확인한다
    if (!hasDetail) {
      getDocument(serverId)
        .then((detail) => fresh() && setMeta(detail))
        .catch((err) => {
          if (fresh() && parseApiError(err).status === 404) setNotFound(true)
        })
    }
    // 화면에 들어올 때 한 번만 불러온다
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 답이 늦으면 안내 문구를 바꾼다
  useEffect(() => {
    if (!pending) return
    setSlow(false)
    const t = setTimeout(() => setSlow(true), SLOW_AFTER)
    return () => clearTimeout(t)
  }, [pending])

  // 새 답변을 글자 단위로 보여주는 효과 (긴 답변도 2초 안에 끝나도록 한 번에 여러 글자씩)
  useEffect(() => {
    if (!reveal) return
    const full = messages.find((m) => m.id === reveal.id)?.text ?? ''
    if (reveal.shown >= full.length) {
      setReveal(null)
      return
    }
    const stepSize = Math.max(3, Math.ceil(full.length / 120))
    const t = setTimeout(() => setReveal((r) => r && { ...r, shown: r.shown + stepSize }), 16)
    return () => clearTimeout(t)
  }, [reveal, messages])

  useEffect(() => {
    if (historyState !== 'ready') return
    if (skipScroll.current) {
      skipScroll.current = false
      return
    }
    endRef.current?.scrollIntoView({ behavior: firstScroll.current ? 'auto' : 'smooth', block: 'end' })
    firstScroll.current = false
  }, [historyState, messages.length, pending, failed, reveal?.shown])

  const loadMore = () => {
    if (!nextPage || loadingMore) return
    setLoadingMore(true)
    fetchChatHistory(serverId, nextPage)
      .then((res) => {
        if (!fresh()) return
        skipScroll.current = true
        setMessages((prev) => mergeChatItems(prev, res.results))
        setNextPage(res.next ? nextPage + 1 : null)
      })
      .catch(() => fresh() && showToast(T('이전 대화를 불러오지 못했어요')))
      .finally(() => aliveRef.current && setLoadingMore(false))
  }

  const status = meta?.status ?? (local ? (LOCAL_STATUS[local.status] ?? 'PROCESSING') : null)
  const lowConfidence = meta?.lowConfidence ?? local?.lowConfidence ?? false
  const blocked = notReady || lowConfidence || (status != null && status !== 'DONE')
  const busy = Boolean(pending) || Boolean(reveal)
  const title = local?.title ?? meta?.title ?? T('문서')

  const send = (text) => {
    const q = text.trim()
    if (!q || busy || blocked) return
    if (q.length > MAX_QUESTION) {
      showToast(
        T('질문은 {v0}자까지 쓸 수 있어요', {
          v0: MAX_QUESTION.toLocaleString(),
        }),
      )
      return
    }
    if (!online) {
      showToast(T('인터넷에 연결되면 질문할 수 있어요'))
      return
    }
    setFailed(null)
    setInput('')
    setPending(q)
    askQuestion(serverId, q, sessionRef.current)
      .then((item) => {
        if (!fresh()) return
        setMessages((prev) => mergeChatItems(prev, [item]))
        if (!prefersReducedMotion()) setReveal({ id: `c${item.chatId}-a`, shown: 0 })
      })
      .catch((err) => {
        if (!fresh() || err instanceof StaleSessionError) return
        if (isChatTimeout(err)) {
          setFailed({ question: q, action: 'reload', text: '답변이 오래 걸려 기다리지 못했어요. 답변이 저장됐을 수 있으니 대화를 다시 불러오세요.' })
          return
        }
        const info = parseApiError(err)
        if (info.code === 'DOCUMENT_NOT_READY' || info.status === 409) {
          setNotReady(true)
          setInput(q)
        } else if (info.status === 404) setNotFound(true)
        else if (info.status === 400) {
          setFailed({ question: q, action: 'edit', text: info.fields.message || info.message || '질문을 확인해 주세요.' })
        } else if (info.code === 'NETWORK_ERROR') {
          setFailed({ question: q, action: 'retry', text: '서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 보내세요.' })
        } else {
          setFailed({ question: q, action: 'retry', text: '답변을 만들지 못했어요. 잠시 후 다시 보내세요.' })
        }
      })
      .finally(() => aliveRef.current && setPending(null))
  }

  const resolveFailed = () => {
    if (!failed) return
    const { question, action } = failed
    if (action === 'retry') send(question)
    else if (action === 'edit') {
      setFailed(null)
      setInput(question)
    } else {
      setFailed(null)
      loadHistory()
        .then(() => showToast(T('대화를 다시 불러왔어요')))
        .catch(() => setFailed({ question, action: 'reload', text: '대화를 불러오지 못했어요. 잠시 후 다시 시도하세요.' }))
    }
  }

  if (notFound) {
    return (
      <>
        <TopBar title={T('문서에 질문하기')} onBack={back} />
        <main className="screen">
          <Empty
            icon={FileX}
            title={T('문서를 찾을 수 없어요')}
            desc={T('삭제됐거나 지금 로그인한 계정의 문서가 아니에요.')}
            action={
              <button className="btn btn-primary" onClick={() => tab('docs')}>
                {T('내 문서로 가기')}
              </button>
            }
          />
        </main>
      </>
    )
  }

  // 문서 정보 줄: 결과가 있으면 결과 화면, 없으면 결과를 불러오는 화면으로
  const openSource = () => {
    if (hasDetail) nav('result', { docId })
    else if (local) openDoc(local, nav, showToast)
    else nav('analyzing', { docId, reopen: true })
  }

  const asked = new Set(messages.filter((m) => m.role === 'user').map((m) => m.text))
  const last = messages[messages.length - 1]
  const lastFollow = last?.role === 'assistant' ? last.follow : []
  const suggestions =
    busy || blocked || failed || historyState !== 'ready'
      ? []
      : lastFollow?.length
        ? lastFollow
        : starterQuestions(contentLang, hasDetail ? local : null)
            .filter((q) => !asked.has(q))
            .slice(0, messages.length ? 3 : 4)

  const blockedText =
    status === 'FAILED'
      ? T('분석하지 못한 문서라 질문할 수 없어요. 문서를 다시 올려 주세요.')
      : lowConfidence
        ? T('글자가 흐려 내용을 확인하지 못한 문서라 질문할 수 없어요. 문서를 밝은 곳에서 다시 촬영해 올려 주세요.')
        : notReady
          ? T('이 문서는 지금 질문할 수 없어요. 문서 해석이 끝났는지 확인한 뒤 다시 시도하세요.')
          : T('문서 분석이 끝나면 질문할 수 있어요.')

  const placeholder = blocked ? T('이 문서에는 질문할 수 없어요') : !online ? T('오프라인에서는 질문할 수 없어요') : T('질문을 입력하세요')

  return (
    <>
      <TopBar title={T('문서에 질문하기')} onBack={back} />
      <main className="screen chat-screen">
        <button className="chat-doc" onClick={openSource}>
          <FileText size={18} />
          <span>
            <small>{T('질문하는 문서')}</small>
            <strong>{title}</strong>
          </span>
        </button>

        {blocked && (
          <p className="callout tone-amber chat-blocked" role="status">
            {blockedText}
          </p>
        )}

        {historyState === 'loading' && (
          <p className="chat-loading" role="status">
            <span className="typing" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            {T('이전 대화를 불러오는 중이에요')}
          </p>
        )}

        {historyState === 'error' && (
          <div className="chat-retry">
            <p>{T('이전 대화를 불러오지 못했어요.')}</p>
            <button className="btn btn-small btn-outline" onClick={() => loadHistory().catch(() => {})}>
              <RefreshCw size={14} /> {T('다시 불러오기')}
            </button>
          </div>
        )}

        {historyState === 'ready' && messages.length === 0 && !pending && !failed && !blocked && (
          <p className="chat-intro">{T('이 문서의 내용, 준비서류, 절차, 기한을 물어보세요. 공식 자료에서 근거를 찾으면 출처와 함께, 찾지 못하면 미확인이라고 알려 드려요.')}</p>
        )}

        {nextPage && (
          <button className="chat-more" onClick={loadMore} disabled={loadingMore}>
            <History size={16} /> {loadingMore ? T('불러오는 중') : T('이전 대화 더 보기')}
          </button>
        )}

        <ul className="messages" aria-live="polite">
          {messages.map((m) => (
            <ServerMessage
              key={m.id}
              m={m}
              text={reveal?.id === m.id ? m.text.slice(0, reveal.shown) : m.text}
              revealing={reveal?.id === m.id}
              doc={hasDetail ? local : null}
              docId={docId}
              setSheet={setSheet}
              nav={nav}
            />
          ))}
          {pending && (
            <>
              <li className="msg is-user">
                <div className="bubble">{pending}</div>
              </li>
              <li className="msg is-assistant">
                <div className="bubble">
                  <span className="typing" aria-label={T('답변 작성 중')}>
                    <i />
                    <i />
                    <i />
                  </span>
                  {slow && <p className="chat-slow">{T('공식 자료를 찾아보고 있어요. 최대 2분까지 걸릴 수 있어요.')}</p>}
                </div>
              </li>
            </>
          )}
          {failed && (
            <li className="msg is-user is-failed">
              <div className="bubble">{failed.question}</div>
              <div className="msg-error" role="alert">
                <p>{T(failed.text)}</p>
                <button className="btn btn-small btn-outline" onClick={resolveFailed}>
                  <RefreshCw size={14} /> {failed.action === 'retry' ? T('다시 보내기') : failed.action === 'edit' ? T('질문 고치기') : T('대화 다시 불러오기')}
                </button>
              </div>
            </li>
          )}
        </ul>

        {suggestions.length > 0 && (
          <div className="suggestions" aria-label={T('추천 질문')}>
            {suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)} disabled={!online}>
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.length > 0 && <p className="chat-note">{T('AI 답변은 참고용이에요. 최종 확인은 담당기관에서 하세요.')}</p>}
        <div ref={endRef} />
      </main>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={placeholder} aria-label={T('질문 입력')} maxLength={MAX_QUESTION} disabled={busy || blocked || !online} />
        <button className="send-btn" type="submit" disabled={!input.trim() || busy || blocked || !online} aria-label={T('보내기')}>
          <Send size={20} />
        </button>
      </form>
    </>
  )
}

/** 근거 상태별 표시 */
const ANSWER_TONE = {
  GROUNDED: { cls: '', label: '공식 근거 있음', Icon: ShieldCheck, labelCls: 'grounded' },
  MISMATCH: { cls: ' is-mismatch', label: '문서와 공식 자료가 달라요', Icon: AlertTriangle, labelCls: 'mismatch' },
  UNVERIFIED: { cls: ' is-ungrounded', label: '미확인 답변', Icon: HelpCircle, labelCls: 'ungrounded' },
}

function ServerMessage({ m, text, revealing, doc, docId, setSheet, nav }) {
  if (m.role === 'user')
    return (
      <li className="msg is-user">
        <div className="bubble">{m.text}</div>
      </li>
    )
  const tone = ANSWER_TONE[m.status] ?? ANSWER_TONE.UNVERIFIED
  const I = tone.Icon
  return (
    <li className={`msg is-assistant${tone.cls}`}>
      <div className="bubble">
        {text}
        {!revealing && (
          <div className="bubble-foot">
            <span className={tone.labelCls}>
              <I size={14} /> {T(tone.label)}
            </span>
            {m.sources.length > 0 && (
              <div className="src-chips">
                {m.sources.map((s, i) => (
                  <button key={`${s.url}-${i}`} className="src-chip" onClick={() => setSheet({ type: 'source', payload: { items: [s] } })}>
                    {T('{v0}, {v1} 확인', {
                      v0: s.title,
                      v1: fmtDate(s.checkedAt),
                    })}
                  </button>
                ))}
              </div>
            )}
            {m.status !== 'GROUNDED' && <AgencyHint doc={doc} docId={docId} nav={nav} mismatch={m.status === 'MISMATCH'} />}
          </div>
        )}
      </div>
    </li>
  )
}

/** 미확인·불일치 답변 아래 담당기관 연결 */
function AgencyHint({ doc, docId, nav, mismatch }) {
  const agency = doc?.agency
  const known = agency?.name && agency.name !== '확인되지 않음'
  return (
    <div className="mini-agency">
      <p className="mini-agency-text">{mismatch ? T('어느 쪽이 맞는지 담당기관에 확인하세요.') : T('정확한 내용은 담당기관에 확인하세요.')}</p>
      {known && <strong>{agency.name}</strong>}
      {(agency?.phone || doc) && (
        <div className="mini-actions">
          {agency?.phone && (
            <a className="btn btn-small btn-primary" href={`tel:${agency.phone}`}>
              <Phone size={14} /> {T('전화')}
            </a>
          )}
          {doc && (
            <button className="btn btn-small btn-outline" onClick={() => nav('agency', { docId })}>
              {T('담당기관 안내')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/* ───────────────────────── 시연용 목업 문서 ───────────────────────── */

function findAnswer(doc, q) {
  const rules = CHAT_KB[doc.templateId] || []
  const lower = q.toLowerCase()
  const hit = rules.find((r) => r.keys.some((k) => lower.includes(k.toLowerCase())))
  if (hit) return { grounded: true, sources: hit.sources, answer: hit.answer, follow: hit.follow }
  return { grounded: false, sources: [], answer: CHAT_FALLBACK, follow: doc.suggestedQuestions.slice(0, 2) }
}

function MockChat({ doc }) {
  const { data, update, back, nav, setSheet, contentLang } = useApp()
  const docId = doc.id
  const messages = data.chats[docId] || []
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(null) // { msg, shown, typing }
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending?.shown, pending?.typing])

  // 답변을 글자 단위로 보여주는 스트리밍 효과
  useEffect(() => {
    if (!pending) return
    if (pending.typing) {
      const t = setTimeout(() => setPending((p) => ({ ...p, typing: false })), 800)
      return () => clearTimeout(t)
    }
    const full = pending.msg.text
    if (pending.shown >= full.length) {
      update((d) => ({ chats: { ...d.chats, [docId]: [...(d.chats[docId] || []), pending.msg] } }))
      setPending(null)
      return
    }
    const t = setTimeout(() => setPending((p) => ({ ...p, shown: p.shown + 3 })), 18)
    return () => clearTimeout(t)
  }, [pending])

  const send = (text) => {
    const q = text.trim()
    if (!q || pending) return
    const userMsg = { id: 'u' + Date.now(), role: 'user', text: q }
    update((d) => ({ chats: { ...d.chats, [docId]: [...(d.chats[docId] || []), userMsg] } }))
    setInput('')
    const r = findAnswer(doc, q)
    const msg = { id: 'a' + Date.now(), role: 'assistant', text: pick(r.answer, contentLang), grounded: r.grounded, sources: r.sources, follow: r.follow }
    setPending({ msg, shown: 0, typing: true })
  }

  const last = messages[messages.length - 1]
  const suggestions = !pending ? (last?.role === 'assistant' ? last.follow : messages.length === 0 ? doc.suggestedQuestions : []) : []

  return (
    <>
      <TopBar title={T('문서에 질문하기')} onBack={back} />
      <main className="screen chat-screen">
        <button className="chat-doc" onClick={() => nav('result', { docId })}>
          <FileText size={18} />
          <span>
            <small>{T('질문하는 문서')}</small>
            <strong>{doc.title}</strong>
          </span>
        </button>

        {messages.length === 0 && !pending && <p className="chat-intro">{T('이 문서의 내용, 준비서류, 절차, 기한에 대해 물어보세요. 공식 자료에서 찾은 근거와 함께 답해 드려요.')}</p>}

        <ul className="messages">
          {messages.map((m) => (
            <MockMessage key={m.id} m={m} doc={doc} setSheet={setSheet} nav={nav} />
          ))}
          {pending && (
            <li className="msg is-assistant">
              <div className="bubble">
                {pending.typing ? (
                  <span className="typing" aria-label={T('답변 작성 중')}>
                    <i />
                    <i />
                    <i />
                  </span>
                ) : (
                  pending.msg.text.slice(0, pending.shown)
                )}
              </div>
            </li>
          )}
        </ul>

        {suggestions?.length > 0 && (
          <div className="suggestions">
            {suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        <div ref={endRef} />
      </main>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={T('질문을 입력하세요')} aria-label={T('질문 입력')} disabled={!!pending} />
        <button className="send-btn" type="submit" disabled={!input.trim() || !!pending} aria-label={T('보내기')}>
          <Send size={20} />
        </button>
      </form>
    </>
  )
}

function MockMessage({ m, doc, setSheet, nav }) {
  if (m.role === 'user')
    return (
      <li className="msg is-user">
        <div className="bubble">{m.text}</div>
      </li>
    )
  return (
    <li className={`msg is-assistant${m.grounded ? '' : ' is-ungrounded'}`}>
      <div className="bubble">
        {m.text}
        {m.grounded ? (
          <div className="bubble-foot">
            <span className="grounded">
              <ShieldCheck size={14} /> {T('공식 근거 있음')}
            </span>
            <div className="src-chips">
              {m.sources.map((s) => (
                <button key={s} className="src-chip" onClick={() => setSheet({ type: 'source', payload: { ids: [s] } })}>
                  {T('{v0}, {v1} 확인', {
                    v0: SOURCES[s].title,
                    v1: fmtDate(SOURCES[s].checkedAt),
                  })}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="bubble-foot">
            <span className="ungrounded">
              <HelpCircle size={14} /> {T('미확인 답변')}
            </span>
            <div className="mini-agency">
              <strong>{doc.agency.name}</strong>
              <div className="mini-actions">
                <a className="btn btn-small btn-primary" href={`tel:${doc.agency.phone}`}>
                  <Phone size={14} /> {T('전화')}
                </a>
                <button className="btn btn-small btn-outline" onClick={() => nav('agency', { docId: doc.id })}>
                  {T('담당기관 안내')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </li>
  )
}
