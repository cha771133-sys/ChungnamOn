import axios from 'axios'
import type { InternalAxiosRequestConfig } from 'axios'

const ACCESS_KEY = 'chungnamon-access'
const REFRESH_KEY = 'chungnamon-refresh'

/** 토큰이 만료돼 다시 로그인해야 할 때 window에 보내는 이벤트 이름 */
export const AUTH_EXPIRED_EVENT = 'chungnamon:auth-expired'

/** 인증 API 경로. 이 요청들의 401은 토큰 갱신 대상이 아니다. */
const AUTH_PATHS = ['/api/auth/login/', '/api/auth/register/', '/api/auth/token/refresh/', '/api/auth/logout/']

/** 저장소에서 값을 읽는다. 사생활 보호 모드 등으로 막혀 있으면 null을 돌려준다. */
function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/**
 * 세션 번호. 로그인·로그아웃·만료 때마다 1씩 올린다.
 * 진행 중이던 토큰 갱신이나 재요청이 끝났을 때 세션이 바뀌었으면 결과를 버려서,
 * 로그아웃 뒤 토큰이 되살아나거나 이전 계정의 요청이 새 계정으로 다시 보내지는 것을 막는다.
 */
let sessionId = 0

/** Access·Refresh 토큰 보관소. PWA에서 로그인이 유지되도록 localStorage를 쓴다. */
export const tokenStore = {
  /** 저장된 Access Token을 돌려준다. */
  getAccess: (): string | null => readStorage(ACCESS_KEY),
  /** 저장된 Refresh Token을 돌려준다. */
  getRefresh: (): string | null => readStorage(REFRESH_KEY),
  /** 현재 세션 번호를 돌려준다. */
  session: (): number => sessionId,
  /** 로그인 성공 시 새 세션을 시작하고 토큰을 저장한다. */
  start(access: string, refresh: string): void {
    sessionId += 1
    writeTokens(access, refresh)
  },
  /** 갱신 시 Refresh Token도 바뀌므로 두 토큰을 항상 함께 교체한다. 세션은 그대로 유지한다. */
  set(access: string, refresh: string): void {
    writeTokens(access, refresh)
  },
  /** 로그아웃·만료 시 세션을 끝내고 두 토큰을 모두 지운다. */
  clear(): void {
    sessionId += 1
    try {
      localStorage.removeItem(ACCESS_KEY)
      localStorage.removeItem(REFRESH_KEY)
    } catch {
      /* 무시 */
    }
  },
}

/** 두 토큰을 저장소에 쓴다. 저장소를 쓸 수 없으면 무시한다. */
function writeTokens(access: string, refresh: string): void {
  try {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  } catch {
    /* 저장소를 쓸 수 없으면 새로고침 후 로그인이 유지되지 않는다 */
  }
}

const baseURL = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({ baseURL, timeout: 15000 })

type SessionConfig = InternalAxiosRequestConfig & { _retry?: boolean; _session?: number }

/** 모든 요청에 저장된 Access Token을 Bearer 헤더로 붙이고, 요청을 보낸 세션 번호를 기록한다. */
api.interceptors.request.use((config: SessionConfig) => {
  if (config._session === undefined) config._session = tokenStore.session()
  const token = tokenStore.getAccess()
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  return config
})

/** 세션이 바뀌어 결과를 버린 경우에 쓰는 오류 */
class StaleSessionError extends Error {
  constructor() {
    super('세션이 바뀌어 요청을 중단했습니다.')
    this.name = 'StaleSessionError'
  }
}

/** 갱신 실패가 "다시 로그인해야 하는" 경우인지 판단한다. 네트워크 오류·서버 오류는 세션을 유지한다. */
function isRefreshRejected(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false
  const status = error.response?.status
  return status === 400 || status === 401 || status === 403
}

let refreshing: { session: number; promise: Promise<string> } | null = null

/**
 * Refresh Token으로 새 토큰 쌍을 받는다.
 * 같은 세션에서 여러 요청이 동시에 401을 받아도 갱신 요청은 한 번만 보내고 결과를 함께 쓴다.
 * 갱신이 끝났을 때 세션이 바뀌었으면(로그아웃·다른 계정 로그인) 새 토큰을 저장하지 않는다.
 */
function refreshTokens(): Promise<string> {
  const session = tokenStore.session()
  if (refreshing && refreshing.session === session) return refreshing.promise

  const refresh = tokenStore.getRefresh()
  const promise = axios
    .post(`${baseURL}/api/auth/token/refresh/`, { refresh }, { timeout: 15000 })
    .then(({ data }) => {
      if (tokenStore.session() !== session) throw new StaleSessionError()
      tokenStore.set(data.access, data.refresh)
      return data.access as string
    })
    .finally(() => {
      if (refreshing?.promise === promise) refreshing = null
    })
  refreshing = { session, promise }
  return promise
}

/** 로그인이 풀렸음을 앱에 알리고 저장된 토큰을 지운다. */
function expireSession(): void {
  tokenStore.clear()
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

/**
 * 401을 받으면 토큰을 한 번 갱신해 원래 요청을 다시 보낸다.
 * - 요청을 보낸 세션이 이미 끝났으면 갱신하지 않고 그대로 실패시킨다.
 * - 서버가 갱신을 거부(400·401·403)하거나 Refresh Token이 없을 때만 세션을 만료시킨다.
 * - 네트워크 오류·시간 초과·서버 오류는 세션을 유지하고 원래 오류만 돌려준다.
 */
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as SessionConfig | undefined
    const isAuthPath = AUTH_PATHS.some((path) => config?.url?.includes(path))
    if (error.response?.status !== 401 || !config || isAuthPath) return Promise.reject(error)
    if (config._session !== tokenStore.session()) return Promise.reject(error)

    if (config._retry || !tokenStore.getRefresh()) {
      expireSession()
      return Promise.reject(error)
    }
    config._retry = true
    try {
      const access = await refreshTokens()
      if (config._session !== tokenStore.session()) return Promise.reject(error)
      config.headers.set('Authorization', `Bearer ${access}`)
      return api(config)
    } catch (refreshError) {
      if (refreshError instanceof StaleSessionError) return Promise.reject(error)
      if (isRefreshRejected(refreshError)) expireSession()
      return Promise.reject(error)
    }
  },
)

export type ApiErrorInfo = {
  /** 백엔드 오류 코드 (예: VALIDATION_ERROR, AUTHENTICATION_FAILED) 또는 NETWORK_ERROR */
  code: string
  /** 백엔드가 보낸 전체 오류 메시지 */
  message: string
  /** 입력칸별 첫 번째 오류 메시지 */
  fields: Record<string, string>
}

/** 백엔드 공통 오류 형식 {code, message, details}를 화면에 쓰기 쉬운 모양으로 바꾼다. */
export function parseApiError(error: unknown): ApiErrorInfo {
  if (!axios.isAxiosError(error)) return { code: 'UNKNOWN', message: '', fields: {} }
  if (!error.response) return { code: 'NETWORK_ERROR', message: '', fields: {} }
  const body = (error.response.data ?? {}) as { code?: string; message?: string; details?: Record<string, unknown> }
  const fields: Record<string, string> = {}
  for (const [key, value] of Object.entries(body.details ?? {})) {
    const first = Array.isArray(value) ? value[0] : value
    if (typeof first === 'string') fields[key] = first
  }
  return { code: body.code ?? 'API_ERROR', message: body.message ?? '', fields }
}
