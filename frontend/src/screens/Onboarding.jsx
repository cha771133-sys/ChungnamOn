import { useState } from 'react'
import { Check, Bell, Camera, Languages, CalendarCheck } from 'lucide-react'
import { useApp } from '../store.js'
import { LANGS, NATIONALITIES, VISAS } from '../data/mock.js'
import { TopBar, Stamp } from '../components/ui.jsx'

export function Intro() {
  const { nav } = useApp()
  return (
    <main className="screen onboard intro">
      <div className="intro-visual" aria-hidden="true">
        <div className="paper">
          <span className="paper-title">납세고지서</span>
          <span className="paper-line w80" />
          <span className="paper-line w60" />
          <span className="paper-line w70" />
          <span className="paper-line w40" />
        </div>
        <div className="intro-stamp">
          <Stamp tone="seal" top="공식 자료" bottom="대조 완료" />
        </div>
      </div>
      <h1 className="display">받은 행정문서, 내 언어로 이해하고 기한까지 챙겨요</h1>
      <ol className="intro-steps">
        <li>
          <Camera size={20} /> 문서를 사진이나 PDF로 올려요
        </li>
        <li>
          <Languages size={20} /> 모국어로 풀어 설명하고 공식 자료와 대조해요
        </li>
        <li>
          <CalendarCheck size={20} /> 할 일과 마감일을 알려드려요
        </li>
      </ol>
      <div className="onboard-actions">
        <button className="btn btn-primary btn-block btn-lg" onClick={() => nav('language')}>
          시작하기
        </button>
      </div>
    </main>
  )
}

export function LanguageSelect() {
  const { data, update, nav, replace, back } = useApp()
  const [sel, setSel] = useState(data.profile.language)
  // 유저 플로우 n8: 로그인 여부에 따라 분기
  const next = () => {
    update((d) => ({ profile: { ...d.profile, language: sel } }))
    if (!data.user) nav('login')
    else if (!data.profile.nationality || !data.profile.visa) replace('profile')
    else replace('notify')
  }
  return (
    <>
      <TopBar onBack={back} />
      <main className="screen onboard">
        <h1 className="h1">사용할 언어를 선택하세요</h1>
        <p className="lead multi">Chọn ngôn ngữ / Choose your language / 选择语言</p>
        <ul className="option-list">
          {LANGS.map((l) => (
            <li key={l.code}>
              <button className={`option${sel === l.code ? ' is-selected' : ''}`} onClick={() => setSel(l.code)} aria-pressed={sel === l.code}>
                <span className="flag" aria-hidden="true">{l.flag}</span>
                <span className="option-text">
                  <strong>{l.label}</strong>
                  <small>{l.ko}</small>
                </span>
                {sel === l.code && <Check size={20} />}
              </button>
            </li>
          ))}
        </ul>
        <div className="sticky-actions">
          <button className="btn btn-primary btn-block btn-lg" onClick={next}>
            다음
          </button>
        </div>
      </main>
    </>
  )
}

function Field({ label, error, children }) {
  return (
    <label className={`field${error ? ' has-error' : ''}`}>
      <span className="field-label">{label}</span>
      {children}
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

export function Login() {
  const { data, update, replace, back } = useApp()
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState({})
  const submit = (e) => {
    e.preventDefault()
    const next = {}
    if (!email.includes('@')) next.email = '이메일 형식을 확인하세요.'
    if (pw.length < 4) next.pw = '비밀번호를 4자 이상 입력하세요.'
    setErr(next)
    if (Object.keys(next).length) return
    update(() => ({ user: { email } }))
    const p = data.profile
    if (p.nationality && p.visa) replace('notify')
    else replace('profile')
  }
  return (
    <>
      <TopBar onBack={back} />
      <main className="screen onboard">
        <h1 className="h1">로그인</h1>
        <p className="lead">올린 문서와 일정을 안전하게 보관하려면 로그인이 필요해요.</p>
        <form onSubmit={submit} noValidate>
          <Field label="이메일" error={err.email}>
            <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </Field>
          <Field label="비밀번호" error={err.pw}>
            <input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <button className="btn btn-primary btn-block btn-lg" type="submit">
            로그인
          </button>
        </form>
        <p className="switch-line">
          처음이신가요?{' '}
          <button className="text-btn" onClick={() => replace('signup')}>
            회원가입
          </button>
        </p>
        <p className="demo-note">데모: 아무 이메일과 4자 이상 비밀번호로 로그인돼요.</p>
      </main>
    </>
  )
}

export function Signup() {
  const { update, replace, back } = useApp()
  const [f, setF] = useState({ email: '', pw: '', pw2: '' })
  /* 유저 플로우 n6 ↔ n7 */
  const [err, setErr] = useState({})
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = (e) => {
    e.preventDefault()
    const next = {}
    if (!f.email.includes('@')) next.email = '이메일 형식을 확인하세요.'
    if (f.pw.length < 8) next.pw = '비밀번호를 8자 이상 입력하세요.'
    if (f.pw !== f.pw2) next.pw2 = '비밀번호가 서로 달라요.'
    setErr(next)
    if (Object.keys(next).length) return
    update(() => ({ user: { email: f.email } }))
    replace('profile')
  }
  return (
    <>
      <TopBar onBack={back} />
      <main className="screen onboard">
        <h1 className="h1">회원가입</h1>
        <form onSubmit={submit} noValidate>
          <Field label="이메일" error={err.email}>
            <input type="email" autoComplete="email" value={f.email} onChange={set('email')} />
          </Field>
          <Field label="비밀번호 (8자 이상)" error={err.pw}>
            <input type="password" autoComplete="new-password" value={f.pw} onChange={set('pw')} />
          </Field>
          <Field label="비밀번호 확인" error={err.pw2}>
            <input type="password" autoComplete="new-password" value={f.pw2} onChange={set('pw2')} />
          </Field>
          <button className="btn btn-primary btn-block btn-lg" type="submit">
            가입하기
          </button>
        </form>
        <p className="switch-line">
          이미 계정이 있나요?{' '}
          <button className="text-btn" onClick={() => replace('login')}>
            로그인
          </button>
        </p>
      </main>
    </>
  )
}

export function ProfileForm({ value, onChange, showErrors }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  return (
    <>
      <Field label="안내 언어">
        <select value={value.language} onChange={set('language')}>
          {LANGS.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label} ({l.ko})
            </option>
          ))}
        </select>
      </Field>
      <Field label="국적" error={showErrors && !value.nationality ? '국적을 선택하세요.' : null}>
        <select value={value.nationality} onChange={set('nationality')}>
          <option value="">선택하세요</option>
          {NATIONALITIES.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </Field>
      <Field label="체류자격" error={showErrors && !value.visa ? '체류자격을 선택하세요.' : null}>
        <select value={value.visa} onChange={set('visa')}>
          <option value="">선택하세요</option>
          {VISAS.map((v) => (
            <option key={v.code} value={v.code}>
              {v.label}
            </option>
          ))}
        </select>
      </Field>
    </>
  )
}

export function ProfileSetup() {
  const { data, update, replace } = useApp()
  const [p, setP] = useState(data.profile)
  const [tried, setTried] = useState(false)
  const save = () => {
    setTried(true)
    if (!p.nationality || !p.visa) return
    update(() => ({ profile: p }))
    replace('notify')
  }
  return (
    <>
      <TopBar />
      <main className="screen onboard">
        <h1 className="h1">내 정보를 알려주세요</h1>
        <p className="lead">국적과 체류자격에 따라 적용되는 절차가 달라서, 맞춤 안내에 사용해요.</p>
        <ProfileForm value={p} onChange={setP} showErrors={tried} />
        <p className="hint">이 정보는 안내를 돕기 위한 것이며 공식 자격 판정에 쓰이지 않아요. 설정에서 언제든 바꿀 수 있어요.</p>
        <div className="sticky-actions">
          <button className="btn btn-primary btn-block btn-lg" onClick={save}>
            저장하고 계속하기
          </button>
        </div>
      </main>
    </>
  )
}

export function NotifyPermission() {
  const { update, tab, setSheet, isStandalone, data } = useApp()
  const finish = (enabled) => {
    update((d) => ({ onboarded: true, notify: { ...d.notify, enabled } }))
    tab('home')
    if (!isStandalone && !data.installDismissed) setTimeout(() => setSheet({ type: 'install' }), 700)
  }
  const allow = async () => {
    if ('Notification' in window) {
      try {
        const p = await Notification.requestPermission()
        finish(p === 'granted')
        return
      } catch {
        /* 권한 요청이 막힌 브라우저 */
      }
    }
    finish(true)
  }
  return (
    <>
      <TopBar />
      <main className="screen onboard">
        <div className="push-preview" aria-hidden="true">
          <div className="push-app">
            <span className="push-icon" />
            ChungnamOn <small>지금</small>
          </div>
          <strong>주민세 내기 마감 7일 전이에요</strong>
          <span>11월 30일까지 아산시청 세무과에 납부하세요.</span>
        </div>
        <h1 className="h1">마감일을 놓치지 않게 알려드릴게요</h1>
        <p className="lead">등록한 일정의 마감 7일 전과 1일 전에 알림을 보내요.</p>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={allow}>
            <Bell size={18} /> 알림 켜기
          </button>
          <button className="btn btn-ghost btn-block" onClick={() => finish(false)}>
            나중에
          </button>
        </div>
      </main>
    </>
  )
}
