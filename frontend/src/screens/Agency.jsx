import { Phone, AlertTriangle, Copy } from 'lucide-react'
import { useApp, fmtDate } from '../store.js'
import { SOURCES } from '../data/mock.js'
import { TopBar, Badge, Disclaimer } from '../components/ui.jsx'

export default function Agency({ docId }) {
  const { data, back, showToast, setSheet } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  if (!doc) return null
  const a = doc.agency
  const src = SOURCES[a.sourceId]
  const conflictSrc = a.conflict ? SOURCES[a.conflict.sourceId] : null

  // 체크리스트의 조건부·확인 필요 항목을 우선 모으고, 없으면 미확인 검증 항목을 보여줌
  const fromChecklist = doc.checklist
    .filter((c) => c.state === 'CONDITIONAL' || c.state === 'NEEDS_CHECK')
    .map((c) => ({ id: c.id, text: c.text, note: c.note, state: c.state }))
  const toAsk = fromChecklist.length
    ? fromChecklist
    : doc.verification.filter((v) => v.status === 'UNVERIFIED').map((v) => ({ id: v.id, text: v.label, note: v.reason, state: v.status }))

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(doc.callScript)
      showToast('문장을 복사했어요')
    } catch {
      showToast('복사하지 못했어요. 길게 눌러 직접 복사하세요.')
    }
  }

  return (
    <>
      <TopBar title="담당기관 안내" onBack={back} />
      <main className="screen">
        <p className="screen-sub">{doc.title}</p>

        <section className="agency-card">
          <span className="agency-basis">문서를 보낸 기관 기준</span>
          <h1 className="h2">{a.name}</h1>
          <a className="btn btn-primary btn-block btn-lg" href={`tel:${a.phone}`}>
            <Phone size={18} /> {a.phone} 전화 걸기
          </a>
          {src && (
            <button className="agency-src" onClick={() => setSheet({ type: 'source', payload: { ids: [a.sourceId] } })}>
              출처: {src.title}, {fmtDate(src.checkedAt)} 확인
            </button>
          )}
        </section>

        {a.conflict && (
          <section className="conflict">
            <div className="conflict-head">
              <AlertTriangle size={18} />
              <strong>연락처가 서로 달라요</strong>
            </div>
            <dl>
              <div>
                <dt>문서에 적힌 번호</dt>
                <dd>
                  <a href={`tel:${a.phone}`}>{a.phone}</a>
                </dd>
              </div>
              <div>
                <dt>{conflictSrc.agency} 홈페이지 번호</dt>
                <dd>
                  <a href={`tel:${a.conflict.phone}`}>{a.conflict.phone}</a>
                </dd>
              </div>
            </dl>
            <p>어느 번호가 맞는지 단정할 수 없어요. 먼저 문서에 적힌 번호로 연락해 보세요.</p>
          </section>
        )}

        {toAsk.length > 0 && (
          <section className="section">
            <div className="section-head">
              <h2>기관에 확인할 것</h2>
            </div>
            <ul className="ask-list">
              {toAsk.map((q) => (
                <li key={q.id}>
                  <div className="ask-top">
                    <strong>{q.text}</strong>
                    <Badge status={q.state} />
                  </div>
                  {q.note && <p>{q.note}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {doc.callScript && (
          <section className="section">
            <div className="section-head">
              <h2>전화나 방문할 때 보여줄 문장</h2>
              <button className="text-btn" onClick={copy}>
                <Copy size={14} /> 복사
              </button>
            </div>
            <blockquote className="script">{doc.callScript}</blockquote>
          </section>
        )}

        <Disclaimer>담당기관 정보는 참고용이에요. 최종 자격, 기한, 제출 방법은 담당기관에서 확인하세요.</Disclaimer>
      </main>
    </>
  )
}
