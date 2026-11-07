import { useEffect, useState } from 'react'
import { Phone, AlertTriangle, Copy, ExternalLink, RefreshCw, SearchX } from 'lucide-react'
import { useApp, fmtDate, getSource, isVisibleDoc, safeHttpUrl } from '../store.js'
import { TopBar, Badge, Disclaimer } from '../components/ui.jsx'
import { findOfficialAgency, phonesDiffer, PHONE_TYPE_LABEL, agencyTitle, buildCallScript } from '../lib/agencyAdapter.js'
import { parseApiError, tokenStore } from '../lib/api'

export default function Agency({ docId }) {
  const { data, back, showToast, setSheet } = useApp()
  const found = data.docs.find((d) => d.id === docId)
  // 다른 계정이 올린 서버 문서는 보여주지 않는다
  const doc = found && isVisibleDoc(found, data.user) ? found : null
  const isServer = doc?.source === 'api'
  const knownName = isServer && doc.agency?.name && doc.agency.name !== '확인되지 않음' ? doc.agency.name : ''
  const official = useOfficialAgency(knownName)
  if (!doc) return null
  const a = doc.agency
  // 서버 문서는 기관별 안내 문장이 없어 문서 정보로 만든다
  const callScript = doc.callScript ?? (isServer ? buildCallScript(doc) : null)
  // 공식 자료에서 찾은 번호가 문서의 번호와 다르면 둘을 나란히 보여준다 (어느 쪽도 단정하지 않음)
  const officialAgency = official.result?.lookupStatus === 'FOUND' ? official.result.agency : null
  const officialConflict = isServer && officialAgency && phonesDiffer(a.phone, officialAgency.phone)
  const src = getSource(doc, a.sourceId)
  const conflictSrc = a.conflict ? getSource(doc, a.conflict.sourceId) : null

  // 체크리스트의 조건부·확인 필요 항목을 우선 모으고, 없으면 미확인 검증 항목을 보여줌
  const fromChecklist = doc.checklist
    .filter((c) => c.state === 'CONDITIONAL' || c.state === 'NEEDS_CHECK')
    .map((c) => ({ id: c.id, text: c.text, note: c.note, state: c.state }))
  const toAsk = fromChecklist.length
    ? fromChecklist
    : doc.verification.filter((v) => v.status === 'UNVERIFIED').map((v) => ({ id: v.id, text: v.label, note: v.reason, state: v.status }))

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(callScript)
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
          {a.phone ? (
            <a className="btn btn-primary btn-block btn-lg" href={`tel:${a.phone}`}>
              <Phone size={18} /> {a.phone} 전화 걸기
            </a>
          ) : (
            <p className="hint">문서에서 연락처를 찾지 못했어요. 문서를 보낸 기관에 직접 문의하세요.</p>
          )}
          {src && (
            <button className="agency-src" onClick={() => setSheet({ type: 'source', payload: { ids: [a.sourceId], docId } })}>
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

        {officialConflict && (
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
                <dt>공식 자료의 {agencyTitle(officialAgency)} 번호</dt>
                <dd>
                  <a href={`tel:${officialAgency.phone}`}>{officialAgency.phone}</a>
                </dd>
              </div>
            </dl>
            <p>어느 번호가 맞는지 단정할 수 없어요. 먼저 문서에 적힌 번호로 연락해 보세요.</p>
          </section>
        )}

        {isServer && <OfficialAgency name={knownName} state={official} docPhone={a.phone} />}

        {callScript && (
          <section className="section">
            <div className="section-head">
              <h2>전화나 방문할 때 보여줄 문장</h2>
              <button className="text-btn" onClick={copy}>
                <Copy size={14} /> 복사
              </button>
            </div>
            <blockquote className="script">{callScript}</blockquote>
          </section>
        )}

        <Disclaimer>담당기관 정보는 참고용이에요. 최종 자격, 기한, 제출 방법은 담당기관에서 확인하세요.</Disclaimer>
      </main>
    </>
  )
}

/** 문서의 기관명으로 공식 담당기관을 찾는다. 이름이 없으면 찾지 않는다. */
function useOfficialAgency(name) {
  const [state, setState] = useState({ status: name ? 'loading' : 'idle', result: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!name) return
    let alive = true
    const session = tokenStore.session()
    findOfficialAgency(name)
      .then((result) => alive && tokenStore.session() === session && setState({ status: 'done', result }))
      .catch((err) => {
        if (!alive) return
        const info = parseApiError(err)
        setState({ status: 'error', result: null, network: info.code === 'NETWORK_ERROR' })
      })
    return () => {
      alive = false
    }
  }, [name, attempt])
  // 다시 찾을 때만 '찾는 중'으로 돌린다 (처음에는 초기 상태가 이미 '찾는 중')
  const retry = () => {
    setState({ status: 'loading', result: null })
    setAttempt((n) => n + 1)
  }
  return { ...state, retry }
}

/** 공식 자료에서 찾은 담당기관 연락처 (서버 문서) */
function OfficialAgency({ name, state, docPhone }) {
  if (!name) {
    return (
      <section className="official">
        <h2 className="official-title">공식 자료에서 찾은 연락처</h2>
        <p className="official-empty">문서에서 보낸 기관을 찾지 못해 공식 연락처를 조회하지 않았어요.</p>
      </section>
    )
  }
  return (
    <section className="official" aria-busy={state.status === 'loading'}>
      <h2 className="official-title">공식 자료에서 찾은 연락처</h2>
      {state.status === 'loading' && <p className="official-empty">공식 자료에서 찾는 중이에요</p>}
      {state.status === 'error' && (
        <div className="official-empty">
          <p>{state.network ? '서버에 연결하지 못했어요.' : '공식 연락처를 불러오지 못했어요.'}</p>
          <button className="btn btn-small btn-outline" onClick={state.retry}>
            <RefreshCw size={14} /> 다시 찾기
          </button>
        </div>
      )}
      {state.status === 'done' && <OfficialResult result={state.result} name={name} docPhone={docPhone} />}
    </section>
  )
}

function OfficialResult({ result, name, docPhone }) {
  if (result.lookupStatus === 'UNVERIFIED') {
    return (
      <div className="official-none">
        <SearchX size={20} />
        <p>
          공식 자료에 “{name}” 연락처가 없어요. 문서에 적힌 연락처로 문의하세요.
        </p>
      </div>
    )
  }
  const list = result.lookupStatus === 'FOUND' ? [result.agency] : result.candidates
  return (
    <>
      {result.lookupStatus === 'AMBIGUOUS' && <p className="official-note is-top">같은 이름의 기관이 여러 곳이에요. 문서에 적힌 지역과 부서를 비교해 보세요.</p>}
      {result.byOrgName && (
        <p className="official-note is-top">
          공식 자료에 문서의 기관명이 그대로 있지 않아, 기관 이름 “{result.orgName}”만으로 찾은 연락처예요. 문서에 적힌 부서와 다를 수 있어요.
        </p>
      )}
      <ul className="official-list">
        {list.map((ag) => (
          <li key={ag.id} className="official-card">
            <div className="official-top">
              <strong>{agencyTitle(ag)}</strong>
              <Badge status="NEEDS_CHECK" />
            </div>
            <span className="official-meta">
              {ag.region}
              {ag.checkedAt ? `, ${fmtDate(ag.checkedAt.slice(0, 10))} 확인` : ', 확인일 없음'}
            </span>
            {ag.phone ? (
              <a className={`btn btn-small ${docPhone ? 'btn-outline' : 'btn-primary'}`} href={`tel:${ag.phone}`}>
                <Phone size={14} /> {ag.phone} <small>({PHONE_TYPE_LABEL[ag.phoneType] ?? PHONE_TYPE_LABEL.UNKNOWN})</small>
              </a>
            ) : (
              <span className="official-meta">공식 자료에 전화번호가 없어요</span>
            )}
            {safeHttpUrl(ag.sourceUrl) && (
              <a className="link-btn" href={safeHttpUrl(ag.sourceUrl)} target="_blank" rel="noreferrer">
                출처 페이지 열기 <ExternalLink size={14} />
              </a>
            )}
          </li>
        ))}
      </ul>
      <p className="official-note">{result.message}</p>
    </>
  )
}
