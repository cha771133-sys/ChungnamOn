import { useEffect, useRef, useState } from 'react'
import { Camera, Image as ImageIcon, FileUp, ShieldCheck, Check, Loader2, RefreshCw, AlertCircle, FileText } from 'lucide-react'
import { useApp } from '../store.js'
import { makeDocFromTemplate } from '../data/mock.js'
import { TopBar } from '../components/ui.jsx'
import { fetchResult, fetchStatus, getDocument, uploadDocument } from '../lib/documents'
import { fetchMe } from '../lib/auth'
import { adaptResult, localDocId, localToday, serverDocId } from '../lib/documentAdapter.js'
import { parseApiError, tokenStore } from '../lib/api'
import { T } from '../i18n.js'

const MAX = 20 * 1024 * 1024
// 서버가 받는 형식과 맞춘다 (jpg, jpeg, png, pdf)
const ALLOWED = /\.(jpe?g|png|pdf)$/i
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'application/pdf']

/** 업로드 화면: 촬영·사진·PDF 중 하나를 골라 실제 서버로 올린다. */
export function Upload() {
  const { back, nav } = useApp()
  const [error, setError] = useState('')

  const onFile = (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    if (f.size > MAX) {
      setError('파일이 20MB보다 커요. 더 작은 파일을 선택하세요.')
      return
    }
    if (!ALLOWED.test(f.name) && !ALLOWED_TYPES.includes(f.type)) {
      setError('jpg, png, pdf 파일만 올릴 수 있어요.')
      return
    }
    setError('')
    nav('analyzing', { file: f, fileName: f.name })
  }

  return (
    <>
      <TopBar title={T('문서 올리기')} onBack={back} />
      <main className="screen">
        <div className="privacy-note">
          <ShieldCheck size={20} />
          <p>{T('외국인등록번호와 주소는 자동으로 가려지고, 원본 파일은 30일 뒤 자동으로 삭제돼요.')}</p>
        </div>

        <div className="upload-options">
          <label className="upload-option is-primary">
            <input type="file" accept="image/jpeg,image/png" capture="environment" onChange={onFile} hidden />
            <Camera size={26} />
            <strong>{T('사진 찍기')}</strong>
            <small>{T('카메라로 문서를 바로 촬영해요')}</small>
          </label>
          <label className="upload-option">
            <input type="file" accept="image/jpeg,image/png" onChange={onFile} hidden />
            <ImageIcon size={24} />
            <strong>{T('사진 선택')}</strong>
          </label>
          <label className="upload-option">
            <input type="file" accept="application/pdf" onChange={onFile} hidden />
            <FileUp size={24} />
            <strong>{T('PDF 선택')}</strong>
          </label>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {T(error)}
          </p>
        )}
        <p className="hint">{T('jpg, png 또는 PDF, 최대 20MB. 한 번에 문서 1개씩 올려요.')}</p>

        <section className="section">
          <h2 className="h3">{T('잘 찍는 방법')}</h2>
          <ul className="tips">
            <li>{T('밝은 곳에서 그림자 없이 찍어요.')}</li>
            <li>{T('문서를 평평하게 펴고 네 모서리가 모두 보이게 해요.')}</li>
            <li>{T('글자가 흐리면 정확하게 해석하기 어려워요.')}</li>
          </ul>
        </section>

        <section className="demo-box">
          <strong>{T('시연용 (목업 데이터, 서버에 올리지 않아요)')}</strong>
          <div className="demo-actions">
            <button className="btn btn-outline" onClick={() => nav('analyzing', { fileName: T('주민세_고지서.jpg'), blur: false })}>
              {T('예시 고지서로 체험')}
            </button>
            <button className="btn btn-outline" onClick={() => nav('analyzing', { fileName: T('흐린_사진.jpg'), blur: true })}>
              {T('흐린 사진으로 체험')}
            </button>
          </div>
        </section>
      </main>
    </>
  )
}

const STEPS = ['글자 읽기', '개인정보 가리기', '내용 해석하기', '공식 자료와 대조하기']

// 서버 처리 단계 → 화면 진행 단계
const STEP_INDEX = { QUEUED: 0, OCR: 0, MASKING: 1, INTERPRETING: 2, VERIFYING: 3, DONE: 4 }
const POLL_INTERVAL = 2500
const SLOW_AFTER = 90000
// 이 시간이 지나도 끝나지 않으면 멈추고 안내한다 (서버 재시작 등으로 처리가 멈춘 경우)
const GIVE_UP_AFTER = 5 * 60 * 1000
// 네트워크 끊김·배포 중 502/503/504 같은 일시 오류는 연속 이 횟수까지 다시 확인한다
const MAX_TRANSIENT_FAILURES = 5

/** 진행 단계 표시 (목업·실제 분석 화면 공통) */
function AnalyzingView({ fileName, step, children, title = T('분석 중') }) {
  return (
    <>
      <TopBar title={title} />
      <main className="screen center-screen">
        <div className="scan" aria-hidden="true">
          <div className="paper small">
            <span className="paper-line w80" />
            <span className="paper-line w60" />
            <span className="paper-line w70" />
            <span className="paper-line w40" />
          </div>
          <span className="scan-bar" />
        </div>
        <p className="file-name">{fileName}</p>
        <ol className="progress-steps" aria-live="polite">
          {STEPS.map((s, i) => (
            <li key={s} className={i < step ? 'is-done' : i === step ? 'is-active' : ''}>
              <span className="step-dot">{i < step ? <Check size={14} /> : i === step ? <Loader2 size={14} className="spin" /> : i + 1}</span>
              {T(s)}
            </li>
          ))}
        </ol>
        {children}
      </main>
    </>
  )
}

/** 분석 화면: 파일(file)이나 이어서 볼 문서(docId)가 있으면 실제 서버, 없으면 시연용 목업으로 동작한다. */
export function Analyzing(props) {
  return props.file || props.docId ? <LiveAnalyzing {...props} /> : <DemoAnalyzing {...props} />
}

/** 시연용 목업 분석: 타이머로 단계를 넘기고 예시 문서를 만든다. */
function DemoAnalyzing({ fileName, blur }) {
  const { update, replace } = useApp()
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (blur && step === 1) {
      const t = setTimeout(() => replace('ocrLow'), 500)
      return () => clearTimeout(t)
    }
    if (step >= STEPS.length) {
      const doc = makeDocFromTemplate(fileName)
      update((d) => ({ docs: [doc, ...d.docs] }))
      replace('result', { docId: doc.id })
      return
    }
    const t = setTimeout(() => setStep((s) => s + 1), 1000)
    return () => clearTimeout(t)
  }, [step])

  return (
    <AnalyzingView fileName={fileName} step={step}>
      <p className="hint">{T('시연용 예시 문서로 분석 과정을 보여드려요.')}</p>
    </AnalyzingView>
  )
}

/**
 * 실제 분석: 파일을 올려 문서 ID를 받고, 2.5초마다 상태를 확인한다.
 * 끝나면 결과를 받아 화면용으로 바꾸고, 글자 인식이 낮으면 재촬영 안내, 실패하면 실패 화면으로 보낸다.
 * 화면을 벗어나도 문서는 문서함에 '분석 중'으로 남고, 다시 열면 이어서 확인한다.
 */
function LiveAnalyzing({ file, fileName, docId, reopen }) {
  const { data, update, updateDoc, replace, tab } = useApp()
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const [slow, setSlow] = useState(false)
  const name = fileName ?? data.docs.find((d) => d.id === docId)?.fileName ?? ''
  const language = data.profile.language
  // 개발 모드(StrictMode)에서 effect가 두 번 실행돼도 업로드는 한 번만 하도록 진행 중인 작업을 기억한다
  const uploadRef = useRef(null)
  // 문서를 올린 계정 ID. StrictMode에서 effect가 다시 실행돼도 같은 값을 쓰도록 ref로 둔다
  const ownerRef = useRef(data.user?.session === tokenStore.marker() ? (data.user?.id ?? null) : null)

  useEffect(() => {
    let alive = true
    let timer = null
    const started = Date.now()
    // 업로드를 시작한 로그인 세션과 계정. 업로드가 끝났을 때 로그아웃·계정 전환이 있었으면 결과를 버린다
    const session = tokenStore.session()
    let failures = 0

    /** 업로드하고 문서함에 '분석 중' 문서를 추가한다. 이어보기면 기존 문서 ID를 쓴다. */
    const ensureUploaded = () => {
      if (docId) return Promise.resolve(docId)
      if (!uploadRef.current) {
        // 계정 ID를 모르면 문서를 계정에 묶을 수 없으므로, 먼저 확인한 뒤에만 업로드한다
        const ensureOwner =
          ownerRef.current != null
            ? Promise.resolve(ownerRef.current)
            : fetchMe().then((me) => {
                // 조회하는 사이 로그아웃·계정 전환이 있었으면 이전 계정 정보를 저장하지 않는다
                if (tokenStore.session() !== session) throw new Error(T('세션이 바뀌어 업로드를 중단했습니다.'))
                ownerRef.current = me.id
                update(() => ({ user: { id: me.id, email: me.email, session: tokenStore.marker() } }))
                return me.id
              })
        // 시작한 세션을 함께 넘겨, 그사이 계정이 바뀌었으면 새 계정 토큰으로 업로드되지 않게 한다
        uploadRef.current = ensureOwner
          .then(() => uploadDocument(file, session))
          .then(({ documentId }) => {
            if (tokenStore.session() !== session) throw new Error(T('세션이 바뀌어 업로드 결과를 버렸습니다.'))
            const id = localDocId(documentId)
            const placeholder = {
              id,
              serverId: documentId,
              ownerId: ownerRef.current,
              source: 'api',
              docType: '문서',
              title: name,
              fileName: name,
              status: 'processing',
              uploadedAt: localToday(),
              createdAt: new Date().toISOString(),
            }
            update((d) => ({ docs: [placeholder, ...d.docs.filter((x) => x.id !== id)] }))
            return id
          })
      }
      return uploadRef.current
    }

    /** 상태를 한 번 확인하고, 끝나지 않았으면 다음 확인을 예약한다. */
    const poll = async (id) => {
      const serverId = serverDocId(id)
      try {
        const res = await fetchStatus(serverId)
        if (!alive) return
        setStep(STEP_INDEX[res.step] ?? 0)
        if (res.status === 'DONE') {
          const raw = await fetchResult(serverId)
          if (!alive) return
          if (tokenStore.session() !== session) return
          const doc = adaptResult(raw, {
            language,
            uploadedAt: data.docs.find((d) => d.id === id)?.uploadedAt ?? localToday(),
            fileName: name,
            // 이어보기는 문서에 이미 기록된 소유 계정을 쓴다
            ownerId: ownerRef.current ?? data.docs.find((d) => d.id === id)?.ownerId,
          })
          // 이 기기에 없던 문서(다른 기기에서 올린 문서)면 새로 추가한다
          update((d) => ({
            docs: d.docs.some((x) => x.id === id) ? d.docs.map((x) => (x.id === id ? { ...x, ...doc } : x)) : [doc, ...d.docs],
          }))
          replace(doc.lowConfidence ? 'ocrLow' : 'result', { docId: id })
          return
        }
        if (res.status === 'FAILED') {
          updateDoc(id, () => ({ status: 'failed', failReason: res.failReason || '' }))
          replace('failed', { docId: id })
          return
        }
        failures = 0
        if (Date.now() - started > SLOW_AFTER) setSlow(true)
      } catch (err) {
        if (!alive) return
        const info = parseApiError(err)
        const transient = info.code === 'NETWORK_ERROR' || info.status >= 500
        // 일시 오류는 정해진 횟수까지 다시 확인하고, 그 밖의 오류는 바로 멈추고 알린다
        if (!transient || ++failures > MAX_TRANSIENT_FAILURES) {
          setError(transient ? '서버와 연결이 불안정해요. 잠시 후 문서함에서 다시 확인하세요.' : info.message || '분석 상태를 확인하지 못했어요.')
          return
        }
      }
      if (Date.now() - started > GIVE_UP_AFTER) {
        setError('분석이 너무 오래 걸리고 있어요. 잠시 후 문서함에서 다시 확인하거나 다시 올려 주세요.')
        return
      }
      timer = setTimeout(() => poll(id), POLL_INTERVAL)
    }

    ensureUploaded()
      .then((id) => alive && poll(id))
      .catch((err) => {
        if (!alive) return
        const info = parseApiError(err)
        if (info.code === 'NETWORK_ERROR') setError('서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 올려 주세요.')
        else setError(info.fields.file || info.message || '문서를 올리지 못했어요.')
      })

    return () => {
      alive = false
      clearTimeout(timer)
    }
    // 업로드·폴링은 화면에 들어올 때 한 번만 시작한다 (의존값이 바뀌어도 다시 올리지 않음)
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (error) {
    return (
      <>
        <TopBar title={T('올리지 못했어요')} onBack={() => replace('upload')} />
        <main className="screen center-screen">
          <div className="fail-icon">
            <AlertCircle size={36} />
          </div>
          <p className="lead" role="alert">
            {T(error)}
          </p>
          <div className="sticky-actions stack">
            <button className="btn btn-primary btn-block btn-lg" onClick={() => replace('upload')}>
              <RefreshCw size={18} /> {T('다시 올리기')}
            </button>
            <button className="btn btn-ghost btn-block" onClick={() => tab('docs')}>
              {T('내 문서로 가기')}
            </button>
          </div>
        </main>
      </>
    )
  }

  return (
    <AnalyzingView fileName={name} step={step} title={reopen ? T('불러오는 중') : T('분석 중')}>
      <p className="hint">
        {reopen ? T('해석 결과를 불러오고 있어요.') : slow ? T('평소보다 오래 걸리고 있어요. 조금만 더 기다려 주세요.') : T('보통 20~40초 걸려요. 화면을 닫아도 문서함에서 이어서 볼 수 있어요.')}
      </p>
      <button className="btn btn-ghost" onClick={() => tab('docs')}>
        <FileText size={18} /> {T('기다리는 동안 문서함 보기')}
      </button>
    </AnalyzingView>
  )
}

/**
 * 글자 인식 신뢰도가 낮을 때의 재촬영 안내.
 * 서버 문서(docId)는 결과를 확정 정보처럼 보여주지 않되, 원하면 주의 문구와 함께 볼 수 있게 한다.
 */
export function OcrLow({ docId }) {
  const { replace, tab } = useApp()
  return (
    <>
      <TopBar title={T('다시 올려 주세요')} onBack={() => tab('home')} />
      <main className="screen">
        <div className="ocr-compare" aria-hidden="true">
          <figure>
            <div className="paper small is-blur">
              <span className="paper-line w80" />
              <span className="paper-line w60" />
              <span className="paper-line w70" />
            </div>
            <figcaption>{T('지금 사진')}</figcaption>
          </figure>
          <figure>
            <div className="paper small">
              <span className="paper-line w80" />
              <span className="paper-line w60" />
              <span className="paper-line w70" />
            </div>
            <figcaption>{T('이렇게 찍어 주세요')}</figcaption>
          </figure>
        </div>
        <h1 className="h2">{T('글자가 잘 보이지 않아 정확하게 해석하기 어려워요')}</h1>
        <p className="lead">{T('기한, 금액, 기관명을 읽지 못했어요. 잘못된 안내를 막기 위해 결과를 보여드리지 않았어요.')}</p>
        <ul className="tips">
          <li>{T('밝은 곳에서 그림자 없이 찍어요.')}</li>
          <li>{T('문서와 휴대폰을 평행하게 맞춰요.')}</li>
        </ul>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={() => replace('upload')}>
            <Camera size={18} /> {T('다시 촬영하기')}
          </button>
          <button className="btn btn-ghost btn-block" onClick={() => replace('upload')}>
            {T('다른 파일 선택')}
          </button>
          {docId && (
            <button className="btn btn-ghost btn-block" onClick={() => replace('result', { docId })}>
              {T('그래도 결과 보기 (정확하지 않을 수 있어요)')}
            </button>
          )}
        </div>
      </main>
    </>
  )
}

/** 처리 실패 화면: 서버가 알려준 실패 사유를 보여주고 다시 올리게 한다. */
export function Failed({ docId }) {
  const { data, back, replace, updateDoc, setSheet } = useApp()
  const doc = data.docs.find((d) => d.id === docId)

  // 목록으로만 받은 실패 문서는 상세 API로 실패 사유를 받아 온다
  useEffect(() => {
    if (!doc?.serverId || doc.failReason) return
    getDocument(doc.serverId)
      .then((detail) => updateDoc(docId, () => ({ failReason: detail.failReason || '' })))
      .catch(() => {
        /* 사유를 못 받아도 기본 안내 문구를 보여준다 */
      })
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [docId])

  return (
    <>
      <TopBar title={T('해석하지 못했어요')} onBack={back} />
      <main className="screen center-screen">
        <div className="fail-icon">
          <AlertCircle size={36} />
        </div>
        <h1 className="h2">{doc?.title}</h1>
        <p className="lead">{doc?.failReason || T('처리 중 문제가 생겼어요.')}</p>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={() => replace('upload')}>
            <RefreshCw size={18} /> {T('다시 올리기')}
          </button>
          {doc?.source === 'api' && (
            <button className="btn btn-ghost btn-block" onClick={() => setSheet({ type: 'deleteDoc', payload: { docId } })}>
              {T('이 문서 삭제')}
            </button>
          )}
        </div>
      </main>
    </>
  )
}
