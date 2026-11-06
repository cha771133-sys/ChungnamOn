import { Check, ExternalLink, Share, PlusSquare, Download, Building2 } from 'lucide-react'
import { useApp, fmtDate, pick, getSource } from '../store.js'
import { LANGS } from '../data/mock.js'
import { t, uiLang } from '../i18n.js'
import { updateMe, hasSession } from '../lib/auth'
import { Sheet, Badge } from './ui.jsx'

/** 공식 근거 자료 카드: 기관, 제목, 확인일, 기준일, 원문 링크 */
export function SourceCard({ id, doc }) {
  const s = getSource(doc, id)
  if (!s) return null
  return (
    <div className="source-card">
      <div className="source-agency">{s.agency}</div>
      <strong>{s.title}</strong>
      <dl className="source-meta">
        <div>
          <dt>확인일</dt>
          <dd>{fmtDate(s.checkedAt)}</dd>
        </div>
        <div>
          <dt>자료 기준일</dt>
          <dd>{s.publishedAt ? fmtDate(s.publishedAt) : '자료에 표시 없음'}</dd>
        </div>
      </dl>
      {!s.publishedAt && <p className="hint">자료에 기준일이 없어 최신 정보인지 판단하기 어려워요.</p>}
      <a className="link-btn" href={s.url} target="_blank" rel="noreferrer">
        원문 페이지 열기 <ExternalLink size={14} />
      </a>
    </div>
  )
}

/** 안내 언어 선택 시트. 로그인 상태면 서버 프로필에도 저장한다. */
function LangSheet() {
  const { data, update, setSheet, showToast } = useApp()
  const lang = data.profile.language
  const choose = (code) => {
    update((d) => ({ profile: { ...d.profile, language: code } }))
    if (hasSession() && data.onboarded) updateMe({ language: code }).catch(() => showToast(t(code, 'errUnknown')))
    setSheet(null)
    showToast(t(code, 'langChanged'))
  }
  return (
    <Sheet title={t(data.profile.language, 'langSheetTitle')} onClose={() => setSheet(null)}>
      <ul className="option-list">
        {LANGS.map((l) => (
          <li key={l.code}>
            <button className={`option${data.profile.language === l.code ? ' is-selected' : ''}`} onClick={() => choose(l.code)}>
              <span className="flag" aria-hidden="true">{l.flag}</span>
              <span className="option-text">
                <strong>{l.label}</strong>
                <small>{l.ko}</small>
              </span>
              {data.profile.language === l.code && <Check size={20} />}
            </button>
          </li>
        ))}
      </ul>
      {uiLang(lang) !== lang && <p className="hint">{t(lang, 'langFallback')}</p>}
    </Sheet>
  )
}

/** 행정용어 풀이 시트 */
function TermSheet({ term }) {
  const { setSheet, contentLang } = useApp()
  // 서버 문서는 해석한 언어 하나로만 풀이가 오므로, 선택 언어가 없으면 있는 풀이를 보여준다
  const local = pick(term.explain, contentLang) ?? Object.values(term.explain).find(Boolean)
  return (
    <Sheet title="행정용어 풀이" onClose={() => setSheet(null)}>
      <p className="term-word">{term.word}</p>
      {local && local !== term.explain.ko && <p className="term-explain">{local}</p>}
      {term.explain.ko && <p className="term-explain-ko">{term.explain.ko}</p>}
    </Sheet>
  )
}

/** 교차 검증 항목의 문서 내용·공식 자료 비교와 근거를 보여주는 시트 */
function EvidenceSheet({ docId, itemId }) {
  const { data, setSheet, nav } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  const item = doc?.verification.find((v) => v.id === itemId)
  if (!item) return null
  const goAgency = () => {
    setSheet(null)
    nav('agency', { docId })
  }
  return (
    <Sheet title={item.label} onClose={() => setSheet(null)}>
      <div className="evidence-status">
        <Badge status={item.status} />
      </div>
      <div className={`compare${item.status === 'MISMATCH' ? ' is-split' : ''}`}>
        <div>
          <span className="compare-label">문서에 적힌 내용</span>
          <strong>{item.docValue}</strong>
        </div>
        {item.officialValue && (
          <div>
            <span className="compare-label">공식 자료</span>
            <strong>{item.officialValue}</strong>
          </div>
        )}
      </div>
      {item.reason && <p className={`callout tone-${item.status === 'MISMATCH' ? 'amber' : 'slate'}`}>{item.reason}</p>}
      {item.sources.length > 0 ? (
        <>
          <h3 className="sheet-sub">근거 자료</h3>
          {item.sources.map((s) => (
            <SourceCard key={s} id={s} doc={doc} />
          ))}
        </>
      ) : (
        <p className="hint">연결된 공식 자료가 없어요.</p>
      )}
      {item.status !== 'MATCHED' && (
        <button className="btn btn-primary btn-block" onClick={goAgency}>
          <Building2 size={18} /> 담당기관에 확인하기
        </button>
      )}
    </Sheet>
  )
}

/** 챗봇 답변 근거 자료 목록 시트 */
function SourceSheet({ ids, docId }) {
  const { setSheet, data } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  return (
    <Sheet title="답변 근거" onClose={() => setSheet(null)}>
      {ids.map((id) => (
        <SourceCard key={id} id={id} doc={doc} />
      ))}
    </Sheet>
  )
}

/** PWA 홈 화면 설치 안내 시트 (안드로이드 설치 버튼, iOS 2단계 안내) */
function InstallSheet() {
  const { installEvt, setInstallEvt, setSheet, update, isIOS, showToast } = useApp()
  const close = () => {
    update(() => ({ installDismissed: true }))
    setSheet(null)
  }
  const install = async () => {
    installEvt.prompt()
    const { outcome } = await installEvt.userChoice
    setInstallEvt(null)
    setSheet(null)
    if (outcome === 'accepted') showToast('홈 화면에 설치했어요')
  }
  return (
    <Sheet title="홈 화면에 추가하기" onClose={close}>
      <p className="lead">홈 화면에 추가하면 앱처럼 바로 열고, 마감 알림도 받을 수 있어요.</p>
      {installEvt ? (
        <button className="btn btn-primary btn-block" onClick={install}>
          <Download size={18} /> 설치하기
        </button>
      ) : isIOS ? (
        <ol className="ios-steps">
          <li>
            <span className="ios-icon"><Share size={20} /></span>
            Safari 아래쪽의 공유 버튼을 누르세요.
          </li>
          <li>
            <span className="ios-icon"><PlusSquare size={20} /></span>
            ‘홈 화면에 추가’를 선택하세요.
          </li>
        </ol>
      ) : (
        <p className="hint">브라우저 메뉴에서 ‘앱 설치’ 또는 ‘홈 화면에 추가’를 선택하세요. 설치 버튼은 빌드 버전(npm run preview)이나 배포 주소에서 나타나요.</p>
      )}
      <button className="btn btn-ghost btn-block" onClick={close}>
        나중에
      </button>
    </Sheet>
  )
}

/** 현재 열린 시트 종류에 맞는 바텀시트를 그린다. */
export default function SheetHost() {
  const { sheet } = useApp()
  switch (sheet.type) {
    case 'lang':
      return <LangSheet />
    case 'term':
      return <TermSheet {...sheet.payload} />
    case 'evidence':
      return <EvidenceSheet {...sheet.payload} />
    case 'source':
      return <SourceSheet {...sheet.payload} />
    case 'install':
      return <InstallSheet />
    default:
      return null
  }
}

