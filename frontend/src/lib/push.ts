import type { AxiosRequestConfig } from 'axios'
import type { Messaging } from '@firebase/messaging'
import { api, StaleSessionError, tokenStore } from './api'

/*
 * 이 기기를 마감 알림 받을 기기로 연결한다 (Firebase Cloud Messaging, FID 방식).
 *
 * 순서 (백엔드 notifications/README.md 계약)
 * 1. 브라우저 알림 권한 확인 → 서비스 워커(/sw.js) 준비
 * 2. register(messaging, { vapidKey, serviceWorkerRegistration }) → onRegistered 콜백으로 FID를 받는다
 * 3. FID를 POST /api/notifications/devices/ 로 보내고, 받은 기기 ID를 계정별로 저장한다
 *    FID가 바뀌면 replacesDeviceId로 이전 기기를 함께 넘긴다
 * 4. 알림을 끄면 DELETE /api/notifications/devices/{id}/, 로그아웃하면 DELETE 후 FCM 등록도 해제한다
 *
 * Firebase 설정(VITE_FIREBASE_*)이 없으면 기능 전체가 꺼지고 상태는 'unconfigured'다.
 * FCM 토큰(getToken) 방식은 서버가 받지 않으므로 쓰지 않는다.
 */

export type PushStatus =
  /** Firebase 설정이 없는 빌드 */
  | 'unconfigured'
  /** 이 브라우저는 웹 푸시를 지원하지 않음 (개발 서버 포함) */
  | 'unsupported'
  /** iPhone·iPad에서 홈 화면에 추가하지 않고 브라우저로 연 경우 */
  | 'needs-install'
  /** 브라우저에서 알림을 차단함 */
  | 'denied'
  /** 아직 알림 권한을 묻지 않음 */
  | 'prompt'
  | 'connecting'
  | 'connected'
  | 'failed'
  /** 알림이 꺼져 있거나 로그인 전 */
  | 'off'

const env = import.meta.env
const firebaseConfig = {
  apiKey: env.VITE_FIREBASE_API_KEY,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: env.VITE_FIREBASE_PROJECT_ID,
  messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: env.VITE_FIREBASE_APP_ID,
}
const vapidKey = env.VITE_FIREBASE_VAPID_KEY

/** 푸시에 꼭 필요한 Firebase 설정이 모두 있는지 */
export const pushConfigured = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.messagingSenderId && firebaseConfig.appId && vapidKey)

const STORE_KEY = 'chungnamon-push-device'
const FID_WAIT_MS = 20000
const LOGOUT_WAIT_MS = 3000

type SavedDevice = { owner: string; deviceId: number; fid: string }
type DeviceResponse = { id: number; isActive: boolean }

// ─────────────────────────────────────────────
// 화면에 알려줄 상태 (useSyncExternalStore로 구독)
// ─────────────────────────────────────────────

let status: PushStatus = pushConfigured ? 'off' : 'unconfigured'
const listeners = new Set<() => void>()

function setStatus(next: PushStatus) {
  if (status === next) return
  status = next
  listeners.forEach((fn) => fn())
}

export function getPushStatus(): PushStatus {
  return status
}

export function subscribePush(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// ─────────────────────────────────────────────
// 기기 저장 (이 브라우저에 등록한 기기 ID, 계정별)
// ─────────────────────────────────────────────

function readSaved(): SavedDevice | null {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    const v = raw ? JSON.parse(raw) : null
    return v && typeof v.owner === 'string' && Number.isInteger(v.deviceId) && typeof v.fid === 'string' ? v : null
  } catch {
    return null
  }
}

function writeSaved(v: SavedDevice | null) {
  try {
    if (v) localStorage.setItem(STORE_KEY, JSON.stringify(v))
    else localStorage.removeItem(STORE_KEY)
  } catch {
    /* 저장 공간이 없어도 다음 실행 때 다시 등록된다 */
  }
}

// ─────────────────────────────────────────────
// 환경 확인
// ─────────────────────────────────────────────

function isIOS(): boolean {
  const ua = navigator.userAgent
  // iPadOS는 Mac처럼 보이므로 터치 지원으로 구분한다
  return /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1)
}

function isStandalone(): boolean {
  return window.matchMedia('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true
}

/** 권한·지원 여부만으로 정해지는 상태. 연결을 시도해도 되면 null */
function blockedStatus(): PushStatus | null {
  if (!pushConfigured) return 'unconfigured'
  // 서비스 워커는 빌드 버전에서만 등록한다 (main.tsx와 같은 조건)
  if (!import.meta.env.PROD) return 'unsupported'
  const hasApis = 'Notification' in window && 'serviceWorker' in navigator && 'PushManager' in window
  if (isIOS() && !isStandalone()) return 'needs-install'
  if (!hasApis) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  if (Notification.permission === 'default') return 'prompt'
  return null
}

// ─────────────────────────────────────────────
// Firebase
// ─────────────────────────────────────────────

type MessagingSdk = typeof import('@firebase/messaging')

let messagingPromise: Promise<{ sdk: MessagingSdk; messaging: Messaging } | null> | null = null
/** SDK 파일을 받지 못한 적이 있는지. 브라우저가 실패한 import를 기억할 수 있어, 다시 시도할 때 새로고침한다 */
let sdkLoadFailed = false

/** Firebase SDK는 알림을 연결할 때만 불러온다 (설정이 없거나 알림을 안 쓰면 내려받지 않음) */
function getMessagingOnce(): Promise<{ sdk: MessagingSdk; messaging: Messaging } | null> {
  if (!messagingPromise) {
    // 지원하지 않는 브라우저(isSupported false)만 null로 기억한다.
    // SDK를 내려받지 못한 경우(오프라인 등)는 기억하지 않고 오류를 넘겨, 상태를 'failed'로 두고 다시 시도할 수 있게 한다
    messagingPromise = (async () => {
      const [{ getApps, initializeApp }, sdk] = await Promise.all([import('@firebase/app'), import('@firebase/messaging')])
      if (!(await sdk.isSupported())) return null
      const app = getApps().find((a) => a.name === 'chungnamon') ?? initializeApp(firebaseConfig, 'chungnamon')
      const messaging = sdk.getMessaging(app)
      // 직접 register()한 뒤뿐 아니라 FID가 바뀌거나 푸시 구독이 바뀔 때도 불린다
      sdk.onRegistered(messaging, (fid) => void enqueue(() => saveDevice(fid)))
      sdk.onUnregistered(messaging, (fid) => void enqueue(() => dropDevice(fid)))
      return { sdk, messaging }
    })()
    messagingPromise.catch(() => {
      messagingPromise = null
      sdkLoadFailed = true
    })
  }
  return messagingPromise
}

async function getServiceWorker(): Promise<ServiceWorkerRegistration> {
  // 이미 등록돼 있으면 같은 등록을 돌려준다
  await navigator.serviceWorker.register('/sw.js')
  return navigator.serviceWorker.ready
}

// ─────────────────────────────────────────────
// 서버 기기 등록·해제
// ─────────────────────────────────────────────

/** 지금 연결하려는 계정. 로그아웃·알림 끄기·계정 전환이면 null이나 다른 값이 된다 */
let owner: { userId: string; session: number } | null = null
/** 서버 등록 요청이 겹치지 않게 차례로 처리한다 */
let queue: Promise<unknown> = Promise.resolve()
/** register() 뒤 FID 저장 결과를 기다리는 쪽 */
let waiter: ((ok: boolean) => void) | null = null

/** FCM 등록·해제와 서버 기기 등록을 차례로 실행한다 (늦게 끝난 해제가 새 등록을 지우지 않게) */
function enqueue<T>(task: () => Promise<T>): Promise<T> {
  const next = queue.then(task, task)
  queue = next.catch(() => {})
  return next
}

function sessionConfig(session: number): AxiosRequestConfig {
  return { _session: session } as AxiosRequestConfig
}

function isCurrent(o: typeof owner): boolean {
  return o !== null && owner === o && tokenStore.session() === o.session
}

/** onRegistered로 받은 FID를 지금 계정의 기기로 서버에 등록한다. */
async function saveDevice(fid: string) {
  const o = owner
  if (!o || !isCurrent(o)) return
  const saved = readSaved()
  // 같은 계정에서 FID만 바뀌었으면 이전 기기를 함께 넘겨 한 번에 교체한다
  const replacesDeviceId = saved && saved.owner === o.userId && saved.fid !== fid ? saved.deviceId : undefined
  try {
    const { data } = await api.post<DeviceResponse>(
      '/api/notifications/devices/',
      { registrationId: fid, registrationType: 'FID', platform: 'WEB', ...(replacesDeviceId ? { replacesDeviceId } : {}) },
      sessionConfig(o.session),
    )
    if (!isCurrent(o)) return
    writeSaved({ owner: o.userId, deviceId: data.id, fid })
    setStatus('connected')
    waiter?.(true)
  } catch (err) {
    if (err instanceof StaleSessionError || !isCurrent(o)) return
    setStatus('failed')
    waiter?.(false)
  }
}

/** FCM에서 이 기기 등록이 풀렸으면 서버에서도 해제한다. */
async function dropDevice(fid: string) {
  const saved = readSaved()
  if (!saved || saved.fid !== fid) return
  writeSaved(null)
  const o = owner
  if (o && isCurrent(o) && saved.owner === o.userId) {
    await deleteDevice(saved.deviceId, o.session).catch(() => {})
    if (isCurrent(o)) setStatus('failed')
  }
}

function deleteDevice(deviceId: number, session: number) {
  return api.delete(`/api/notifications/devices/${deviceId}/`, sessionConfig(session))
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T | undefined> {
  return Promise.race([p, new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), ms))])
}

// ─────────────────────────────────────────────
// 화면에서 부르는 함수
// ─────────────────────────────────────────────

/**
 * 알림이 켜진 로그인 계정이면 이 기기를 연결한다. 앱을 열 때마다 불러 FID 변경·계정 전환을 맞춘다.
 * 권한 요청은 하지 않는다 (사용자가 누른 버튼에서 requestPushPermission으로 요청).
 */
export async function connectPush(userId: string | number): Promise<PushStatus> {
  const blocked = blockedStatus()
  const o = { userId: String(userId), session: tokenStore.session() }
  owner = o
  if (blocked) {
    setStatus(blocked)
    return blocked
  }
  setStatus('connecting')
  try {
    const fcm = await getMessagingOnce()
    if (!isCurrent(o)) return status
    if (!fcm) {
      setStatus('unsupported')
      return status
    }
    const serviceWorkerRegistration = await getServiceWorker()
    if (!isCurrent(o)) return status
    // 이 호출의 대기만 정리한다 (끄고 바로 켠 경우 새 호출의 대기를 지우지 않게)
    let mine: ((ok: boolean) => void) | null = null
    const done = new Promise<boolean>((resolve) => {
      mine = resolve
      waiter = resolve
      setTimeout(() => resolve(false), FID_WAIT_MS)
    })
    try {
      // 앞서 걸린 해제(로그아웃·세션 만료)가 끝난 뒤, 이 계정이 그대로일 때만 등록한다
      // 앞선 해제가 멈춰 차례가 오지 않아도 '연결 중'에 갇히지 않게 기다리는 시간을 제한한다.
      // 시간이 지나면 '실패'로 보여 주고, 등록은 대기열에 남아 차례가 오면 실행된다 (성공하면 '연결됨'으로 바뀜)
      const registered = await withTimeout(
        enqueue(async () => {
          if (!isCurrent(o)) return false
          await fcm.sdk.register(fcm.messaging, { vapidKey, serviceWorkerRegistration })
          return true
        }),
        FID_WAIT_MS,
      )
      if (registered === undefined) {
        if (isCurrent(o)) setStatus('failed')
        return status
      }
      if (!registered) return status
      const ok = await done
      if (isCurrent(o) && !ok) setStatus('failed')
    } finally {
      if (waiter === mine) waiter = null
    }
  } catch {
    if (isCurrent(o)) setStatus('failed')
  }
  return status
}

/** '다시 연결' 버튼: SDK 파일을 받지 못해 실패했으면 새로고침해서 다시 받고, 아니면 바로 다시 연결한다. */
export function retryPush(userId: string | number): Promise<PushStatus> {
  if (sdkLoadFailed) {
    window.location.reload()
    return Promise.resolve(status)
  }
  return connectPush(userId)
}

/** 버튼을 눌렀을 때 브라우저 알림 권한을 요청하고, 허용되면 연결한다. */
export async function requestPushPermission(userId: string | number): Promise<PushStatus> {
  if ('Notification' in window && Notification.permission === 'default') {
    try {
      await Notification.requestPermission()
    } catch {
      /* 권한 요청이 막힌 브라우저 */
    }
  }
  return connectPush(userId)
}

/**
 * 알림이 꺼진 계정일 때: 이 기기에 연결 기록이 있으면 서버에서 해제한다 (FCM 등록은 다시 켤 때 그대로 쓴다).
 * 다른 기기에서 알림을 끈 경우에도 앱을 열 때 이 기기 연결이 정리된다.
 */
export function disconnectPush(userId: string | number): Promise<void> {
  const session = tokenStore.session()
  owner = null
  waiter = null
  setStatus(pushConfigured ? 'off' : 'unconfigured')
  return enqueue(async () => {
    const saved = readSaved()
    if (!saved) return
    writeSaved(null)
    if (saved.owner === String(userId) && tokenStore.getRefresh()) await deleteDevice(saved.deviceId, session).catch(() => {})
  })
}

/**
 * 로그아웃 직전에 부른다. 아직 로그인된 토큰으로 기기를 해제하고 FCM 등록도 지운다.
 * 오래 걸려도 로그아웃이 막히지 않게 각 단계를 몇 초만 기다린다.
 */
export async function releasePushForLogout(): Promise<void> {
  const session = tokenStore.session()
  owner = null
  waiter = null
  setStatus(pushConfigured ? 'off' : 'unconfigured')
  const saved = readSaved()
  writeSaved(null)
  if (saved && tokenStore.getRefresh())
    await withTimeout(
      deleteDevice(saved.deviceId, session).catch(() => {}),
      LOGOUT_WAIT_MS,
    )
  // 로그아웃은 몇 초만 기다린다. 해제 작업은 순서 체인에 남아 나중에라도 끝까지 실행된다
  if (messagingPromise) await withTimeout(unregisterInQueue(session), LOGOUT_WAIT_MS)
}

/**
 * FCM 등록 해제를 등록과 같은 순서 체인에 넣는다. 해제가 끝나기 전에 다시 로그인해도 새 등록은 해제 뒤에 실행된다.
 * 체인 안에서는 실제 해제가 끝날 때까지 기다린다 (시간 제한으로 먼저 넘어가면, 늦게 끝난 해제가 새 등록을 지울 수 있음).
 * 기다리는 시간 제한은 부르는 쪽(로그아웃)에서 둔다.
 */
function unregisterInQueue(session: number): Promise<void> {
  const pending = messagingPromise
  return enqueue(async () => {
    if (!pending) return
    const fcm = await pending.catch(() => null)
    // 차례가 오기 전에 다른 계정으로 새로 로그인했으면 그 계정이 이 등록을 이어 쓰므로 지우지 않는다.
    // 로그아웃만 된 경우(세션 번호는 바뀌었지만 토큰이 없음)에는 예약한 해제를 그대로 실행한다
    const reloggedIn = tokenStore.session() !== session && Boolean(tokenStore.getRefresh())
    if (fcm && !reloggedIn) await fcm.sdk.unregister(fcm.messaging).catch(() => {})
  })
}

/**
 * 세션이 만료돼 서버에 해제 요청을 보낼 수 없을 때: 저장한 기기 정보를 지우고 FCM 등록도 해제한다.
 * 다음 로그인 때 새 FID로 다시 등록된다.
 */
export function forgetPush(): void {
  owner = null
  waiter = null
  writeSaved(null)
  setStatus(pushConfigured ? 'off' : 'unconfigured')
  // 서버에서는 해제할 수 없지만, FCM 등록을 지워 이 브라우저로 이전 계정 알림이 더 오지 않게 한다
  if (messagingPromise) void unregisterInQueue(tokenStore.session())
}

/** 서비스 워커가 알림 문구를 안내 언어로 보여주도록 알려준다. */
export function syncPushLanguage(lang: string): void {
  if (!pushConfigured || !import.meta.env.PROD || !('serviceWorker' in navigator)) return
  navigator.serviceWorker.ready.then((reg) => reg.active?.postMessage({ type: 'SET_LANG', lang })).catch(() => {})
}
