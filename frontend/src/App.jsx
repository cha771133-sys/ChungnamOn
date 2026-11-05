import { useEffect, useState } from 'react'
import { Ctx, CONTENT_LANGS } from './store.js'
import { INITIAL_DOCS, INITIAL_SCHEDULES } from './data/mock.js'
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

const KEY = 'chungnamon-demo-v1'

function load() {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

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

export default function App() {
  const [data, setData] = useState(() => load() || fresh())
  const [stack, setStack] = useState(() => {
    if (!data.onboarded) return [{ name: 'language', params: {} }]
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
    return () => {
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
  const updateDoc = (docId, fn) =>
    setData((d) => ({ ...d, docs: d.docs.map((doc) => (doc.id === docId ? { ...doc, ...fn(doc) } : doc)) }))
  const showToast = (msg) => {
    setToast(msg)
    clearTimeout(window.__toastTimer)
    window.__toastTimer = setTimeout(() => setToast(null), 2400)
  }

  const lang = data.profile.language
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
    resetDemo: () => {
      setData(fresh())
      setStack([{ name: 'language', params: {} }])
      showToast('데모 데이터를 처음 상태로 되돌렸어요')
    },
  }

  const Screen = SCREENS[cur.name] || Home

  return (
    <Ctx.Provider value={ctx}>
      <div className="app">
        {!online && (
          <div className="offline" role="status">
            <WifiOff size={16} /> 오프라인 상태예요. 저장된 문서는 계속 볼 수 있어요.
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
