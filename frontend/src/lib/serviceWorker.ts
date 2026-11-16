let registration: Promise<ServiceWorkerRegistration> | null = null

/** PWA와 FCM이 같은 루트 서비스 워커 등록을 공유한다. */
export function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!registration) {
    registration = navigator.serviceWorker.register('/firebase-messaging-sw.js', {
      scope: '/',
      updateViaCache: 'none',
    })
    registration.catch(() => { registration = null })
  }
  return registration
}
