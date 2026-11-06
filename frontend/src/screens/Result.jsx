import { useState } from 'react'
import { ListChecks, MessageCircle, ShieldCheck, ChevronRight, Building2, Trash2 } from 'lucide-react'
import { useApp, getSource, dday, ddayLabel, fmtDate, pick, addDays, isVisibleDoc } from '../store.js'
import { LANGS } from '../data/mock.js'
import { TopBar, Badge, Stamp, Disclaimer } from '../components/ui.jsx'

/* 원문 속 행정용어는 버튼으로, 가린 개인정보(●)는 마스킹 표시로 바꿔 렌더링 */
function RichText({ text, terms = [], onTerm }) {
  const words = terms.map((t) => t.word).sort((a, b) => b.length - a.length)
  // 서버 용어에는 괄호 등 정규식 특수문자가 들어 있을 수 있어 그대로 찾도록 이스케이프한다
  const escape = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // 빈 문자열이 섞이면 모든 위치에 일치해 원문이 글자 단위로 쪼개지므로 제외한다
  const valid = words.filter((w) => typeof w === 'string' && w.length > 0)
  const re = valid.length ? new RegExp(`(${valid.map(escape).join('|')})`, 'g') : null
  // 목업은 ●, 서버는 *로 개인정보를 가린다
  const renderMask = (part, key) =>
    part.split(/(●+|\*{3,})/).map((p, i) =>
      /^(●|\*{3})/.test(p) ? (
        <span key={`${key}-${i}`} className="mask" aria-label="가려진 개인정보">
          {p}
        </span>
      ) : (
        p
      )
    )
  return text.split('\n').map((line, li) => (
    <p key={li}>
      {(re ? line.split(re) : [line]).map((part, pi) => {
        const term = terms.find((t) => t.word === part)
        if (term && onTerm)
          return (
            <button key={pi} className="term" onClick={() => onTerm(term)}>
              {part}
            </button>
          )
        return renderMask(part, `${li}-${pi}`)
      })}
    </p>
  ))
}

/** 문서 해석 결과 화면: 요약, 핵심 정보, 공식 자료 대조, 번역·원문, 행정용어 */
export default function Result({ docId }) {
  const { data, back, nav, setSheet, contentLang, lang } = useApp()
  const found = data.docs.find((d) => d.id === docId)
  // 다른 계정이 올린 서버 문서는 이 계정에 보여주지 않는다
  const doc = found && isVisibleDoc(found, data.user) ? found : null
  // 서버 문서는 해석할 때의 프로필 언어 하나로만 번역이 오므로 그 언어로 보여준다
  const [view, setView] = useState(doc?.contentLang ?? contentLang)
  const [showOriginal, setShowOriginal] = useState(false)
  if (!doc) return null

  const v = doc.verification
  const matched = v.filter((x) => x.status === 'MATCHED').length
  const hasMis = v.some((x) => x.status === 'MISMATCH')
  const hasUn = v.some((x) => x.status === 'UNVERIFIED')
  const tone = hasMis ? 'amber' : hasUn ? 'slate' : 'green'
  const n = doc.deadline ? dday(doc.deadline) : null
  const viewOptions = doc.source === 'api' ? [doc.contentLang] : contentLang === 'ko' ? ['ko'] : [contentLang, 'ko']
  const label = (code) => (code === 'ko' ? '한국어(쉬운 말)' : LANGS.find((l) => l.code === code)?.label)
  const openTerm = (term) => setSheet({ type: 'term', payload: { term } })
  const openEvidence = (itemId) => setSheet({ type: 'evidence', payload: { docId, itemId } })

  return (
    <>
      <TopBar
        title="해석 결과"
        onBack={back}
        right={
          doc.source === 'api' && (
            <button className="icon-btn danger" onClick={() => setSheet({ type: 'deleteDoc', payload: { docId } })} aria-label="문서 삭제">
              <Trash2 size={20} />
            </button>
          )
        }
      />
      <main className="screen has-actions">
        <header className="result-head">
          <div>
            <span className="doc-type-tag">{doc.docType}</span>
            <h1 className="result-title">{doc.title}</h1>
            <p className="result-sender">{doc.sender.name}에서 보낸 문서</p>
          </div>
          <Stamp tone={tone} top={`${matched}/${v.length}`} bottom={hasMis ? '불일치 있음' : hasUn ? '일부 미확인' : '공식 자료 일치'} />
        </header>

        {doc.lowConfidence && (
          <p className="callout tone-amber" role="alert">
            글자가 흐려 일부 내용이 정확하지 않을 수 있어요. 기한과 금액은 원문과 꼭 함께 확인하세요.
          </p>
        )}

        {doc.source !== 'api' && !['vi', 'en', 'ko'].includes(lang) && <p className="hint">선택한 언어는 데모에서 영어로 보여드려요.</p>}

        {viewOptions.length > 1 && (
          <div className="segmented" role="tablist" aria-label="보기 언어">
            {viewOptions.map((code) => (
              <button key={code} role="tab" aria-selected={view === code} className={view === code ? 'is-active' : ''} onClick={() => setView(code)}>
                {label(code)}
              </button>
            ))}
          </div>
        )}

        <section className="summary" aria-label="핵심 요약">
          <ul>
            {pick(doc.summary, view).map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </section>

        <section className="facts" aria-label="핵심 정보">
          <div className="fact">
            <span className="fact-label">해야 할 일</span>
            <strong>{pick(doc.task, view)}</strong>
          </div>
          <div className={`fact${n !== null && n <= 7 ? ' is-urgent' : ''}`}>
            <span className="fact-label">마감일</span>
            {doc.deadline ? (
              <>
                <strong>{fmtDate(doc.deadline)}</strong>
                <span className="fact-dday">{ddayLabel(n)}</span>
              </>
            ) : (
              <strong className="muted">확인되지 않음</strong>
            )}
          </div>
          <div className="fact">
            <span className="fact-label">보낸 기관</span>
            <strong>{doc.sender.name}</strong>
          </div>
          <div className="fact">
            <span className="fact-label">준비서류</span>
            <strong>{doc.requiredDocs.length ? doc.requiredDocs.join(', ') : '확인되지 않음'}</strong>
          </div>
        </section>

        <section className="section">
          <div className="section-head">
            <h2>공식 자료와 대조한 결과</h2>
          </div>
          <ul className="verify-list">
            {v.map((item) => (
              <li key={item.id}>
                <button className={`verify-row tone-${item.status}`} onClick={() => openEvidence(item.id)}>
                  <span className="verify-main">
                    <span className="verify-label">{item.label}</span>
                    <strong>{item.docValue}</strong>
                    <span className="verify-src">
                      {item.sources.length
                        ? `${getSource(doc, item.sources[0]).agency} 자료, ${fmtDate(getSource(doc, item.sources[0]).checkedAt)} 확인`
                        : '공식 자료를 찾지 못했어요'}
                    </span>
                  </span>
                  <Badge status={item.status} />
                  <ChevronRight size={18} className="chev" />
                </button>
              </li>
            ))}
          </ul>
          {(hasMis || hasUn) && (
            <button className="banner tone-amber" onClick={() => nav('agency', { docId })}>
              <Building2 size={18} />
              <span>확인이 필요한 항목이 있어요. 담당기관에 확인하세요.</span>
              <ChevronRight size={18} />
            </button>
          )}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>{showOriginal ? '원문 (한국어)' : '전체 번역'}</h2>
            <button className="text-btn" onClick={() => setShowOriginal(!showOriginal)}>
              {showOriginal ? '번역 보기' : '원문 보기'}
            </button>
          </div>
          <div className={`doc-text${showOriginal ? ' is-original' : ''}`}>
            {showOriginal ? <RichText text={doc.original} terms={doc.terms} onTerm={openTerm} /> : <RichText text={pick(doc.translated, view)} />}
          </div>
          {showOriginal && <p className="hint">밑줄 친 행정용어를 누르면 쉬운 설명을 볼 수 있어요.</p>}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>어려운 행정용어</h2>
          </div>
          <ul className="term-list">
            {doc.terms.map((t) => (
              <li key={t.word}>
                <button onClick={() => openTerm(t)}>
                  <strong>{t.word}</strong>
                  <span>{pick(t.explain, view)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <div className="privacy-note">
          <ShieldCheck size={20} />
          <p>
            개인정보 {doc.maskedCount}곳을 가렸어요. 원본 파일은 {fmtDate(addDays(doc.uploadedAt, 30))}에 자동 삭제돼요.
          </p>
        </div>

        <Disclaimer />
      </main>

      <div className="bottom-actions">
        <button className="btn btn-outline" onClick={() => nav('chat', { docId })}>
          <MessageCircle size={18} /> 질문하기
        </button>
        {/* 유저 플로우 n34: 절차 정보가 충분하면 체크리스트, 아니면 담당기관 안내 */}
        {doc.checklist.length > 0 ? (
          <button className="btn btn-primary" onClick={() => nav('checklist', { docId })}>
            <ListChecks size={18} /> 할 일 체크리스트
          </button>
        ) : (
          <button className="btn btn-primary" onClick={() => nav('agency', { docId })}>
            <Building2 size={18} /> 담당기관 안내
          </button>
        )}
      </div>
    </>
  )
}
