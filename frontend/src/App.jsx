import { useEffect, useRef, useState } from 'react'
import { AUTH_EXPIRED_EVENT, SESSION_CHANGED_EVENT, tokenStore } from './lib/api'
import { fetchMe, hasSession, logout as apiLogout } from './lib/auth'
import { t, setUiLang } from './i18n.js'
import { Ctx, CONTENT_LANGS, purgeServerData } from './store.js'
import { INITIAL_DOCS, INITIAL_SCHEDULES, NATIONALITIES } from './data/mock.js'
import { WifiOff } from 'lucide-react'
import SheetHost from './components/Sheets.jsx'
import { Intro, LanguageSelect, Login, Signup, ProfileSetup, NotifyPermission } from './screens/Onboarding.jsx'
import Home from './screens/Home.jsx'
import Docs from './screens/Docs.jsx'
import { Upload, Analyzing, OcrLow, Failed } from './screens/Upload.jsx'
import Result from './screens/Result.jsx'
import Checklist from './screens/Checklist.jsx'
import Agency from './screens/Agency.jsx'
import Chat from './screens/Chat.jsx'
import { Schedule, ScheduleNew, ScheduleDetail } from './screens/Schedule.jsx'
import Settings from './screens/Settings.jsx'
import { T } from './i18n.js'

const KEY = 'chungnamon-demo-v1'

/** 예전 버전에서 한국어 이름(베트남)으로 저장한 국적을 서버 형식 코드(VN)로 바꾼다. */
function migrate(saved) {
  const nat = saved.profile?.nationality
  const found = NATIONALITIES.find((n) => n.ko === nat)
  if (found) saved.profile.nationality = found.code
  return saved
}

/** 브라우저에 저장된 앱 상태를 불러온다. 없거나 깨져 있으면 null. */
function load() {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? migrate(JSON.parse(raw)) : null
  } catch {
    return null
  }
}

/** 처음 설치했을 때의 앱 상태 */
function fresh() {
  return {
    onboarded: false,
    user: null,
    profile: { language: 'vi', nationality: '', visa: '' },
    docs: INITIAL_DOCS,
    schedules: INITIAL_SCHEDULES,
    chats: {},
    notify: { enabled: false, d7: true, d1: true },
    installDismissed: false,
  }
}

const SCREENS = {
  intro: Intro,
  language: LanguageSelect,
  login: Login,
  signup: Signup,
  profile: ProfileSetup,
  notify: NotifyPermission,
  home: Home,
  docs: Docs,
  schedule: Schedule,
  settings: Settings,
  upload: Upload,
  analyzing: Analyzing,
  ocrLow: OcrLow,
  failed: Failed,
  result: Result,
  checklist: Checklist,
  agency: Agency,
  chat: Chat,
  scheduleNew: ScheduleNew,
  scheduleDetail: ScheduleDetail,
}

/** 앱 전체 상태와 화면 이동(스택)을 관리하는 최상위 컴포넌트 */
export default function App() {
  const [data, setData] = useState(() => load() || fresh())
  const [stack, setStack] = useState(() => {
    if (!data.onboarded) return [{ name: 'language', params: {} }]
    // 온보딩은 마쳤지만 토큰이 없으면(로그아웃·만료) 로그인부터
    if (!hasSession()) return [{ name: 'login', params: {} }]
    // 유저 플로우 n3 → n44 → n39: 마감 알림을 누르면 /?schedule=ID 로 열려 일정 상세로 이동
    const sid = new URLSearchParams(window.location.search).get('schedule')
    if (sid && data.schedules.some((s) => s.id === sid)) {
      window.history.replaceState(null, '', '/')
      return [
        { name: 'home', params: {} },
        { name: 'scheduleDetail', params: { scheduleId: sid } },
      ]
    }
    return [{ name: 'home', params: {} }]
  })
  const [sheet, setSheet] = useState(null)
  const [toast, setToast] = useState(null)
  const [online, setOnline] = useState(navigator.onLine)
  const [installEvt, setInstallEvt] = useState(null)
  // 이벤트 핸들러에서 최신 언어를 읽기 위한 참조
  const langRef = useRef(data.profile.language)
  useEffect(() => {
    langRef.current = data.profile.language
  }, [data.profile.language])

  // 저장된 사용자 정보가 없거나 다른 세션(이전 계정·다른 탭 로그인)의 것이면 현재 토큰으로 다시 확인한다.
  // 계정이 다르면 이전 계정의 서버 문서를 지운다. 확인 전까지 서버 문서는 isVisibleDoc에서 숨겨진다.
  const [verifyAttempt, setVerifyAttempt] = useState(0)
  useEffect(() => {
    const marker = tokenStore.marker()
    if (!hasSession() || (data.user?.id != null && data.user.session === marker)) return
    const session = tokenStore.session()
    fetchMe()
      .then((me) => {
        // 조회하는 사이 로그아웃·계정 전환이 있었으면 이전 계정 정보를 저장하지 않는다
        if (tokenStore.session() !== session) return
        setData((d) => ({
          ...d,
          ...(d.user?.id !== me.id ? purgeServerData(d) : {}),
          user: { id: me.id, email: me.email, session: marker },
        }))
      })
      .catch(() => {
        /* 실패하면 인터넷이 다시 연결될 때나 화면에서 다시 시도할 때 확인한다 */
      })
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [verifyAttempt])

  // 인터넷이 다시 연결되면 계정 확인을 다시 시도한다
  useEffect(() => {
    const onOnline = () => setVerifyAttempt((n) => n + 1)
    window.addEventListener('online', onOnline)
    return () => window.removeEventListener('online', onOnline)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(data))
    } catch {
      /* 저장 공간이 없어도 화면은 계속 동작 */
    }
  }, [data])

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    const bip = (e) => {
      e.preventDefault()
      setInstallEvt(e)
    }
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    window.addEventListener('beforeinstallprompt', bip)
    // 토큰 갱신까지 실패하면 로그인 화면으로 보낸다
    const expired = () => {
      setData((d) => ({ ...d, ...purgeServerData(d), user: null }))
      setStack([{ name: 'login', params: {} }])
      setToast(t(langRef.current, 'sessionExpired'))
      clearTimeout(window.__toastTimer)
      window.__toastTimer = setTimeout(() => setToast(null), 2400)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, expired)
    // 다른 탭에서 계정이 바뀌면, 그 탭이 정리한 저장소 상태를 다시 읽도록 새로고침한다
    const sessionChanged = () => window.location.reload()
    window.addEventListener(SESSION_CHANGED_EVENT, sessionChanged)
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, expired)
      window.removeEventListener(SESSION_CHANGED_EVENT, sessionChanged)
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
      window.removeEventListener('beforeinstallprompt', bip)
    }
  }, [])

  const cur = stack[stack.length - 1]
  const top = () => window.scrollTo(0, 0)
  const nav = (name, params = {}) => {
    setStack((s) => [...s, { name, params }])
    top()
  }
  const replace = (name, params = {}) => {
    setStack((s) => [...s.slice(0, -1), { name, params }])
    top()
  }
  const back = () => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s))
  const tab = (name) => {
    setStack([{ name, params: {} }])
    top()
  }
  const update = (fn) => setData((d) => ({ ...d, ...fn(d) }))
  const updateDoc = (docId, fn) => setData((d) => ({ ...d, docs: d.docs.map((doc) => (doc.id === docId ? { ...doc, ...fn(doc) } : doc)) }))
  const showToast = (msg) => {
    setToast(msg)
    clearTimeout(window.__toastTimer)
    window.__toastTimer = setTimeout(() => setToast(null), 2400)
  }

  const lang = data.profile.language
  // 화면 문구 T()·날짜 형식이 쓰는 안내 언어. 하위 화면을 그리기 전에 맞춘다
  setUiLang(lang)
  const contentLang = CONTENT_LANGS.includes(lang) ? lang : 'en'
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent)

  const ctx = {
    data,
    update,
    updateDoc,
    nav,
    replace,
    back,
    tab,
    sheet,
    setSheet,
    showToast,
    lang,
    contentLang,
    online,
    installEvt,
    setInstallEvt,
    isStandalone,
    isIOS,
    openLang: () => setSheet({ type: 'lang' }),
    /** 저장된 사용자 정보가 현재 세션과 맞지 않을 때 계정 확인을 다시 시도한다 */
    reverifyUser: () => setVerifyAttempt((n) => n + 1),
    resetDemo: () => {
      apiLogout().catch(() => {})
      setData(fresh())
      setStack([{ name: 'language', params: {} }])
      showToast(T('데모 데이터를 처음 상태로 되돌렸어요'))
    },
  }

  const Screen = SCREENS[cur.name] || Home

  return (
    <Ctx.Provider value={ctx}>
      <div className="app">
        {!online && (
          <div className="offline" role="status">
            <WifiOff size={16} /> {T('오프라인 상태예요. 저장된 문서는 계속 볼 수 있어요.')}
          </div>
        )}
        <Screen key={stack.length + cur.name + JSON.stringify(cur.params)} {...cur.params} />
        {sheet && <SheetHost />}
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
      </div>
    </Ctx.Provider>
  )
}
