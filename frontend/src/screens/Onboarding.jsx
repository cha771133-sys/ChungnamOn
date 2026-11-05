import { useRef, useState } from 'react'
import { Check, Bell, Camera, Languages, Globe } from 'lucide-react'
import { useApp } from '../store.js'
import { LANGS, NATIONALITIES, VISAS } from '../data/mock.js'
import { t, uiLang, NATION_NAMES, VISA_NAMES } from '../i18n.js'
import { TopBar, Stamp } from '../components/ui.jsx'

/* 로그인 여부에 따라 다음 단계 결정 (유저 플로우 n8) */
function useAfterIntro() {
  const { data, nav, replace } = useApp()
  return () => {
    if (!data.user) nav('login')
    else if (!data.profile.nationality || !data.profile.visa) replace('profile')
    else replace('notify')
  }
}

/* ① 첫 화면: 언어 선택 — 외국인 사용자가 처음부터 자기 언어로 볼 수 있게 맨 앞에 둠 */
export function LanguageSelect() {
  const { data, update, nav } = useApp()
  const [sel, setSel] = useState(data.profile.language)
  const fallback = uiLang(sel) !== sel
  const next = () => {
    update((d) => ({ profile: { ...d.profile, language: sel } }))
    nav('intro')
  }
  return (
    <main className="screen onboard lang-first">
      <div className="intro-top">
        <span className="brand">
          <span className="brand-mark" aria-hidden="true" />
          ChungnamOn
        </span>
      </div>
      <div className="lang-hero" aria-hidden="true">
        <Globe size={30} />
      </div>
      <h1 className="h1" lang={uiLang(sel)}>
        {t(sel, 'langTitle')}
      </h1>
      <p className="lead multi">Chọn ngôn ngữ / Choose language / 选择语言 / 언어 선택</p>
      <ul className="option-list">
        {LANGS.map((l) => (
          <li key={l.code}>
            <button className={`option${sel === l.code ? ' is-selected' : ''}`} onClick={() => setSel(l.code)} aria-pressed={sel === l.code} lang={l.code}>
              <span className="flag" aria-hidden="true">
                {l.flag}
              </span>
              <span className="option-text">
                <strong>{l.label}</strong>
                <small>{l.ko}</small>
              </span>
              {sel === l.code && <Check size={20} />}
            </button>
          </li>
        ))}
      </ul>
      {fallback && <p className="hint">{t(sel, 'langFallback')}</p>}
      <div className="sticky-actions">
        <button className="btn btn-primary btn-block btn-lg" onClick={next}>
          {t(sel, 'continue')}
        </button>
      </div>
    </main>
  )
}

const SLIDES = [
  {
    key: 's1',
    visual: () => (
      <div className="v-camera">
        <div className="paper">
          <span className="paper-title">안내문</span>
          <span className="paper-line w80" />
          <span className="paper-line w60" />
          <span className="paper-line w70" />
          <span className="paper-line w40" />
        </div>
        <span className="corner tl" />
        <span className="corner tr" />
        <span className="corner bl" />
        <span className="corner br" />
        <span className="shutter">
          <Camera size={28} />
        </span>
      </div>
    ),
  },
  {
    key: 's2',
    visual: (lang, active) => (
      <div className="v-verify">
        <div className="paper">
          <span className="paper-title">납세고지서</span>
          <span className="paper-line w80" />
          <span className="paper-line w60" />
          <span className="paper-line w70" />
          <span className="paper-line w40" />
        </div>
        <span className="translate-chip">
          <Languages size={16} /> {t(lang, 'chip')}
        </span>
        {active && (
          <div className="intro-stamp">
            <Stamp tone="seal" top="공식 자료" bottom="대조 완료" />
          </div>
        )}
      </div>
    ),
  },
  {
    key: 's3',
    visual: (lang) => (
      <div className="v-plan">
        <div className="mini-deadline">
          <span>{t(lang, 'upcoming')}</span>
          <b>D-7</b>
          <strong>{t(lang, 'taskTax')}</strong>
        </div>
        <ul className="mini-checks">
          <li className="is-done">
            <i>
              <Check size={13} strokeWidth={3} />
            </i>
            {t(lang, 'chkCard')}
          </li>
          <li>
            <i />
            {t(lang, 'chkPay')}
          </li>
        </ul>
        <div className="mini-push">
          <Bell size={15} /> {t(lang, 'push7')}
        </div>
      </div>
    ),
  },
]

/* ② 서비스 소개 3장 — 선택한 언어로 표시, 위에서 언어 다시 바꾸기 가능 */
export function Intro() {
  const { lang, openLang, back } = useApp()
  const goNext = useAfterIntro()
  const [i, setI] = useState(0)
  const startX = useRef(null)
  const last = i === SLIDES.length - 1
  const L = LANGS.find((l) => l.code === lang)
  const next = () => (last ? goNext() : setI(i + 1))
  const onTouchStart = (e) => {
    startX.current = e.touches[0].clientX
  }
  const onTouchEnd = (e) => {
    if (startX.current === null) return
    const dx = e.changedTouches[0].clientX - startX.current
    if (dx < -50 && !last) setI(i + 1)
    if (dx > 50 && i > 0) setI(i - 1)
    startX.current = null
  }

  return (
    <main className="screen intro" lang={uiLang(lang)}>
      <div className="intro-top">
        <button className="icon-btn" onClick={back} aria-label={t(lang, 'back')}>
          <span className="back-chev" aria-hidden="true" />
        </button>
        <div className="topbar-right">
          <button className="lang-btn" onClick={openLang}>
            <Globe size={15} />
            <span>{L?.label}</span>
          </button>
          {!last && (
            <button className="text-btn skip-btn" onClick={goNext}>
              {t(lang, 'skip')}
            </button>
          )}
        </div>
      </div>

      <div className="slides" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} aria-roledescription="carousel">
        <div className="slide-track" style={{ transform: `translateX(-${i * 100}%)` }}>
          {SLIDES.map((s, idx) => (
            <section key={s.key} className="slide" aria-hidden={idx !== i} aria-roledescription="slide" aria-label={`${idx + 1} / ${SLIDES.length}`}>
              <div className="slide-visual" aria-hidden="true">
                {s.visual(lang, idx === i)}
              </div>
              <h1 className="display">{t(lang, `${s.key}Title`)}</h1>
              <p className="lead">{t(lang, `${s.key}Desc`)}</p>
            </section>
          ))}
        </div>
      </div>

      <div className="dots">
        {SLIDES.map((s, idx) => (
          <button key={s.key} className={`dot${idx === i ? ' is-active' : ''}`} onClick={() => setI(idx)} aria-label={`${idx + 1} / ${SLIDES.length}`} aria-current={idx === i ? 'step' : undefined} />
        ))}
      </div>

      <div className="onboard-actions">
        <button className="btn btn-primary btn-block btn-lg" onClick={next}>
          {last ? t(lang, 'start') : t(lang, 'continue')}
        </button>
      </div>
    </main>
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
  const { data, update, replace, back, lang } = useApp()
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState({})
  const submit = (e) => {
    e.preventDefault()
    const next = {}
    if (!email.includes('@')) next.email = t(lang, 'errEmail')
    if (pw.length < 4) next.pw = t(lang, 'errPw4')
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
      <main className="screen onboard" lang={uiLang(lang)}>
        <h1 className="h1">{t(lang, 'loginTitle')}</h1>
        <p className="lead">{t(lang, 'loginLead')}</p>
        <form onSubmit={submit} noValidate>
          <Field label={t(lang, 'email')} error={err.email}>
            <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </Field>
          <Field label={t(lang, 'password')} error={err.pw}>
            <input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <button className="btn btn-primary btn-block btn-lg" type="submit">
            {t(lang, 'loginTitle')}
          </button>
        </form>
        <p className="switch-line">
          {t(lang, 'newHere')}{' '}
          <button className="text-btn" onClick={() => replace('signup')}>
            {t(lang, 'signup')}
          </button>
        </p>
        <p className="demo-note">{t(lang, 'demoNote')}</p>
      </main>
    </>
  )
}

export function Signup() {
  const { update, replace, back, lang } = useApp()
  const [f, setF] = useState({ email: '', pw: '', pw2: '' })
  const [err, setErr] = useState({})
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = (e) => {
    e.preventDefault()
    const next = {}
    if (!f.email.includes('@')) next.email = t(lang, 'errEmail')
    if (f.pw.length < 8) next.pw = t(lang, 'errPw8')
    if (f.pw !== f.pw2) next.pw2 = t(lang, 'errPwMatch')
    setErr(next)
    if (Object.keys(next).length) return
    update(() => ({ user: { email: f.email } }))
    replace('profile')
  }
  return (
    <>
      <TopBar onBack={back} />
      <main className="screen onboard" lang={uiLang(lang)}>
        <h1 className="h1">{t(lang, 'signup')}</h1>
        <form onSubmit={submit} noValidate>
          <Field label={t(lang, 'email')} error={err.email}>
            <input type="email" autoComplete="email" value={f.email} onChange={set('email')} />
          </Field>
          <Field label={t(lang, 'pw8')} error={err.pw}>
            <input type="password" autoComplete="new-password" value={f.pw} onChange={set('pw')} />
          </Field>
          <Field label={t(lang, 'pwConfirm')} error={err.pw2}>
            <input type="password" autoComplete="new-password" value={f.pw2} onChange={set('pw2')} />
          </Field>
          <button className="btn btn-primary btn-block btn-lg" type="submit">
            {t(lang, 'join')}
          </button>
        </form>
        <p className="switch-line">
          {t(lang, 'haveAccount')}{' '}
          <button className="text-btn" onClick={() => replace('login')}>
            {t(lang, 'loginTitle')}
          </button>
        </p>
      </main>
    </>
  )
}

/* 국적·체류자격 이름은 한국어 원문과 선택 언어를 함께 표시 */
function localName(map, key, ko, lang) {
  const l = uiLang(lang)
  if (l === 'ko') return ko
  const local = map[key]?.[l]
  return local ? `${local} (${ko})` : ko
}

export function ProfileForm({ value, onChange, showErrors, lang = 'ko' }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  return (
    <>
      <Field label={t(lang, 'fLang')}>
        <select value={value.language} onChange={set('language')}>
          {LANGS.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label} ({l.ko})
            </option>
          ))}
        </select>
      </Field>
      <Field label={t(lang, 'fNat')} error={showErrors && !value.nationality ? t(lang, 'errNat') : null}>
        <select value={value.nationality} onChange={set('nationality')}>
          <option value="">{t(lang, 'choose')}</option>
          {NATIONALITIES.map((n) => (
            <option key={n} value={n}>
              {localName(NATION_NAMES, n, n, lang)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t(lang, 'fVisa')} error={showErrors && !value.visa ? t(lang, 'errVisa') : null}>
        <select value={value.visa} onChange={set('visa')}>
          <option value="">{t(lang, 'choose')}</option>
          {VISAS.map((v) => (
            <option key={v.code} value={v.code}>
              {uiLang(lang) === 'ko' ? v.label : `${v.code} ${VISA_NAMES[v.code]?.[uiLang(lang)] ?? ''} (${v.label})`}
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
  const lang = p.language
  const save = () => {
    setTried(true)
    if (!p.nationality || !p.visa) return
    update(() => ({ profile: p }))
    replace('notify')
  }
  return (
    <>
      <TopBar />
      <main className="screen onboard" lang={uiLang(lang)}>
        <h1 className="h1">{t(lang, 'profileTitle')}</h1>
        <p className="lead">{t(lang, 'profileLead')}</p>
        <ProfileForm value={p} onChange={setP} showErrors={tried} lang={lang} />
        <p className="hint">{t(lang, 'profileHint')}</p>
        <div className="sticky-actions">
          <button className="btn btn-primary btn-block btn-lg" onClick={save}>
            {t(lang, 'saveContinue')}
          </button>
        </div>
      </main>
    </>
  )
}

export function NotifyPermission() {
  const { update, tab, setSheet, isStandalone, data, lang } = useApp()
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
      <main className="screen onboard" lang={uiLang(lang)}>
        <div className="push-preview" aria-hidden="true">
          <div className="push-app">
            <span className="push-icon" />
            ChungnamOn <small>{t(lang, 'now')}</small>
          </div>
          <strong>{t(lang, 'pushTitle')}</strong>
          <span>{t(lang, 'pushBody')}</span>
        </div>
        <h1 className="h1">{t(lang, 'notifyTitle')}</h1>
        <p className="lead">{t(lang, 'notifyLead')}</p>
        <div className="sticky-actions stack">
          <button className="btn btn-primary btn-block btn-lg" onClick={allow}>
            <Bell size={18} /> {t(lang, 'allow')}
          </button>
          <button className="btn btn-ghost btn-block" onClick={() => finish(false)}>
            {t(lang, 'later')}
          </button>
        </div>
      </main>
    </>
  )
}
