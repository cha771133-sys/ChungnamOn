import { useEffect, useState } from 'react'
import { Camera, Image as ImageIcon, FileUp, ShieldCheck, Check, Loader2, RefreshCw, AlertCircle } from 'lucide-react'
import { useApp } from '../store.js'
import { makeDocFromTemplate } from '../data/mock.js'
import { TopBar } from '../components/ui.jsx'

const MAX = 20 * 1024 * 1024

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
    setError('')
    nav('analyzing', { fileName: f.name, blur: false })
  }

  return (
    <>
      <TopBar title="문서 올리기" onBack={back} />
      <main className="screen">
        <div className="privacy-note">
          <ShieldCheck size={20} />
          <p>외국인등록번호와 주소는 자동으로 가려지고, 원본 파일은 30일 뒤 자동으로 삭제돼요.</p>
        </div>

        <div className="upload-options">
          <label className="upload-option is-primary">
            <input type="file" accept="image/*" capture="environment" onChange={onFile} hidden />
            <Camera size={26} />
            <strong>사진 찍기</strong>
            <small>카메라로 문서를 바로 촬영해요</small>
          </label>
          <label className="upload-option">
            <input type="file" accept="image/*" onChange={onFile} hidden />
            <ImageIcon size={24} />
            <strong>사진 선택</strong>
          </label>
          <label className="upload-option">
            <input type="file" accept="application/pdf" onChange={onFile} hidden />
            <FileUp size={24} />
            <strong>PDF 선택</strong>
          </label>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <p className="hint">이미지 또는 PDF, 최대 20MB. 한 번에 문서 1개씩 올려요.</p>

        <section className="section">
          <h2 className="h3">잘 찍는 방법</h2>
          <ul className="tips">
            <li>밝은 곳에서 그림자 없이 찍어요.</li>
            <li>문서를 평평하게 펴고 네 모서리가 모두 보이게 해요.</li>
            <li>글자가 흐리면 정확하게 해석하기 어려워요.</li>
          </ul>
        </section>

        <section className="demo-box">
          <strong>시연용</strong>
          <div className="demo-actions">
            <button className="btn btn-outline" onClick={() => nav('analyzing', { fileName: '주민세_고지서.jpg', blur: false })}>
              예시 고지서로 체험
            </button>
            <button className="btn btn-outline" onClick={() => nav('analyzing', { fileName: '흐린_사진.jpg', blur: true })}>
              흐린 사진으로 체험
            </button>
          </div>
        </section>
      </main>
    </>
  )
}

const STEPS = ['글자 읽기', '개인정보 가리기', '내용 해석하기', '공식 자료와 대조하기']

export function Analyzing({ fileName, blur }) {
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
    <>
      <TopBar title="분석 중" />
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
              {s}
            </li>
          ))}
        </ol>
        <p className="hint">보통 20~40초 걸려요. 화면을 닫아도 문서함에서 이어서 볼 수 있어요.</p>
      </main>
    </>
  )
}

export function OcrLow() {
  const { replace, tab } = useApp()
  return (
    <>
      <TopBar title="다시 올려 주세요" onBack={() => tab('home')} />
      <main className="screen">
        <div className="ocr-compare" aria-hidden="true">
          <figure>
            <div className="paper small is-blur">
              <span className="paper-line w80" />
              <span className="paper-line w60" />
              <span className="paper-line w70" />
            </div>
            <figcaption>지금 사진</figcaption>
          </figure>
          <figure>
            <div className="paper small">
              <span className="paper-line w80" />
              <span className="paper-line w60" />
              <span className="paper-line w70" />
            </div>
            <figcaption>이렇게 찍어 주세요</figcaption>
          </figure>
        </div>
        <h1 className="h2">글자가 잘 보이지 않아 정확하게 해석하기 어려워요</h1>
        <p className="lead">기한, 금액, 기관명을 읽지 못했어요. 잘못된 안내를 막기 위해 결과를 보여드리지 않았어요.</p>
        <ul className="tips">
          <li>밝은 곳에서 그림자 없이 찍어요.</li>
          <li>문서와 휴대폰을 평행하게 맞춰요.</li>
        </ul>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={() => replace('upload')}>
            <Camera size={18} /> 다시 촬영하기
          </button>
          <button className="btn btn-ghost btn-block" onClick={() => replace('upload')}>
            다른 파일 선택
          </button>
        </div>
      </main>
    </>
  )
}

export function Failed({ docId }) {
  const { data, back, replace } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  return (
    <>
      <TopBar title="해석하지 못했어요" onBack={back} />
      <main className="screen center-screen">
        <div className="fail-icon">
          <AlertCircle size={36} />
        </div>
        <h1 className="h2">{doc?.title}</h1>
        <p className="lead">{doc?.failReason || '처리 중 문제가 생겼어요.'}</p>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={() => replace('upload')}>
            <RefreshCw size={18} /> 다시 올리기
          </button>
        </div>
      </main>
    </>
  )
}
