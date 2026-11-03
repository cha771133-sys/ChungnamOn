import { useEffect, useRef, useState } from 'react'
import { Send, ShieldCheck, HelpCircle, Phone, FileText } from 'lucide-react'
import { useApp, pick, fmtDate } from '../store.js'
import { CHAT_KB, CHAT_FALLBACK, SOURCES } from '../data/mock.js'
import { TopBar } from '../components/ui.jsx'

function findAnswer(doc, q) {
  const rules = CHAT_KB[doc.templateId] || []
  const lower = q.toLowerCase()
  const hit = rules.find((r) => r.keys.some((k) => lower.includes(k.toLowerCase())))
  if (hit) return { grounded: true, sources: hit.sources, answer: hit.answer, follow: hit.follow }
  return { grounded: false, sources: [], answer: CHAT_FALLBACK, follow: doc.suggestedQuestions.slice(0, 2) }
}

export default function Chat({ docId }) {
  const { data, update, back, nav, setSheet, contentLang } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
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

  if (!doc) return null

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
      <TopBar title="문서에 질문하기" onBack={back} />
      <main className="screen chat-screen">
        <button className="chat-doc" onClick={() => nav('result', { docId })}>
          <FileText size={18} />
          <span>
            <small>질문하는 문서</small>
            <strong>{doc.title}</strong>
          </span>
        </button>

        {messages.length === 0 && !pending && (
          <p className="chat-intro">이 문서의 내용, 준비서류, 절차, 기한에 대해 물어보세요. 공식 자료에서 찾은 근거와 함께 답해 드려요.</p>
        )}

        <ul className="messages">
          {messages.map((m) => (
            <Message key={m.id} m={m} doc={doc} setSheet={setSheet} nav={nav} />
          ))}
          {pending && (
            <li className="msg is-assistant">
              <div className="bubble">
                {pending.typing ? (
                  <span className="typing" aria-label="답변 작성 중">
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
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="질문을 입력하세요" aria-label="질문 입력" disabled={!!pending} />
        <button className="send-btn" type="submit" disabled={!input.trim() || !!pending} aria-label="보내기">
          <Send size={20} />
        </button>
      </form>
    </>
  )
}

function Message({ m, doc, setSheet, nav }) {
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
              <ShieldCheck size={14} /> 공식 근거 있음
            </span>
            <div className="src-chips">
              {m.sources.map((s) => (
                <button key={s} className="src-chip" onClick={() => setSheet({ type: 'source', payload: { ids: [s] } })}>
                  {SOURCES[s].title}, {fmtDate(SOURCES[s].checkedAt)} 확인
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="bubble-foot">
            <span className="ungrounded">
              <HelpCircle size={14} /> 미확인 답변
            </span>
            <div className="mini-agency">
              <strong>{doc.agency.name}</strong>
              <div className="mini-actions">
                <a className="btn btn-small btn-primary" href={`tel:${doc.agency.phone}`}>
                  <Phone size={14} /> 전화
                </a>
                <button className="btn btn-small btn-outline" onClick={() => nav('agency', { docId: doc.id })}>
                  담당기관 안내
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </li>
  )
}
