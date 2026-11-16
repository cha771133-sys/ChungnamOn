// ChungnamOn 서비스 워커: 앱 화면은 네트워크 우선, 정적 파일은 캐시 우선
const CACHE = 'chungnamon-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== SETTINGS).map((k) => caches.delete(k))))
  )
  self.clients.claim()
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)

  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/index.html')))
    return
  }
  if (url.origin !== location.origin) return

  e.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(req, copy))
          return res
        })
    )
  )
})

// ─────────────────────────────────────────────
// 마감 알림 푸시 (Firebase Cloud Messaging)
// 서버 메시지: notification { title, body } + data { type: 'DEADLINE_REMINDER', scheduleId, daysBefore, deadlineDate, url }
// 잠금 화면 문구는 서버가 한국어로 보내므로, 앱이 알려준 안내 언어가 있으면 그 언어로 바꿔 보여준다.
// 문구에는 업무명·금액 같은 문서 내용이 들어가지 않는다.
// ─────────────────────────────────────────────

// 안내 언어는 화면 캐시와 따로 저장한다 (activate에서 지우지 않음)
const SETTINGS = 'chungnamon-settings'
const LANG_KEY = '/__push-lang'

const REMINDER_TEXT = {
  ko: { title: 'ChungnamOn 마감 알림', 7: '등록한 일정의 마감일이 7일 남았습니다.', 1: '등록한 일정의 마감일이 내일입니다.' },
  en: { title: 'ChungnamOn deadline reminder', 7: 'A deadline you saved is 7 days away.', 1: 'A deadline you saved is tomorrow.' },
  vi: { title: 'ChungnamOn nhắc hạn', 7: 'Còn 7 ngày nữa là đến hạn bạn đã lưu.', 1: 'Ngày mai là hạn bạn đã lưu.' },
}

self.addEventListener('message', (e) => {
  if (e.data?.type !== 'SET_LANG' || typeof e.data.lang !== 'string') return
  e.waitUntil(caches.open(SETTINGS).then((c) => c.put(LANG_KEY, new Response(e.data.lang))))
})

async function savedLang() {
  try {
    const res = await caches.open(SETTINGS).then((c) => c.match(LANG_KEY))
    const lang = res ? await res.text() : ''
    // 화면 번역이 없는 언어(zh·uz·ne·th)는 앱 화면처럼 영어로 보여준다
    return REMINDER_TEXT[lang] ? lang : lang ? 'en' : null
  } catch {
    return null
  }
}

/** 같은 출처의 경로만 연다 (외부 주소로 이동하지 않음) */
function safePath(url) {
  try {
    const u = new URL(url || '/', self.location.origin)
    return u.origin === self.location.origin ? u.pathname + u.search : '/'
  } catch {
    return '/'
  }
}

function readPush(e) {
  try {
    return e.data ? e.data.json() : {}
  } catch {
    return {}
  }
}

async function showPush(payload) {
  // FCM은 notification·data를 나눠 보내고, 예전 형식은 최상위에 title·body·url을 둔다
  const note = payload.notification || payload
  const data = payload.data || payload
  let title = note.title || 'ChungnamOn'
  let body = note.body || ''
  const days = String(data.daysBefore || '')
  if (data.type === 'DEADLINE_REMINDER' && (days === '7' || days === '1')) {
    const lang = await savedLang()
    if (lang) {
      title = REMINDER_TEXT[lang].title
      body = REMINDER_TEXT[lang][days]
    }
  }
  return self.registration.showNotification(title, {
    body,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    // 같은 일정의 같은 알림은 하나로 합친다
    tag: data.scheduleId ? `deadline-${data.scheduleId}-${days}` : undefined,
    data: { url: safePath(data.url) },
  })
}

self.addEventListener('push', (e) => {
  e.waitUntil(showPush(readPush(e)))
})

// 알림을 누르면 열려 있는 앱 창을 그 일정으로 옮기고, 없으면 새로 연다 (앱이 /?schedule=ID를 일정 상세로 연다)
self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  const path = safePath(e.notification.data?.url)
  e.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      const win = wins.find((w) => new URL(w.url).origin === self.location.origin)
      if (win) {
        try {
          const moved = await win.navigate(path)
          if (moved) return moved.focus()
        } catch {
          /* 이 창을 옮길 수 없으면 새 창으로 */
        }
      }
      return self.clients.openWindow(path)
    })()
  )
})
