// ChungnamOn 서비스 워커: 앱 화면은 네트워크 우선, 정적 파일은 캐시 우선
const CACHE = 'chungnamon-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
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

// 마감 알림 푸시 수신 (백엔드 FCM / Web Push 연동 시 사용)
self.addEventListener('push', (e) => {
  // 백엔드는 url을 '/?schedule=일정ID' 형태로 보내면 앱이 일정 상세로 바로 열립니다
  const data = e.data ? e.data.json() : { title: 'ChungnamOn', body: '마감일이 다가오고 있어요' }
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icons/icon-192.png',
      data: { url: data.url || '/' },
    })
  )
})

self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  e.waitUntil(self.clients.openWindow(e.notification.data?.url || '/'))
})
