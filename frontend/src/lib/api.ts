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

/** Access·Refresh 토큰 보관소. PWA에서 로그인이 유지되도록 localStorage를 쓴다. */
export const tokenStore = {
  /** 저장된 Access Token을 돌려준다. */
  getAccess: (): string | null => readStorage(ACCESS_KEY),
  /** 저장된 Refresh Token을 돌려준다. */
  getRefresh: (): string | null => readStorage(REFRESH_KEY),
  /** 갱신 시 Refresh Token도 바뀌므로 두 토큰을 항상 함께 교체한다. */
  set(access: string, refresh: string): void {
    try {
      localStorage.setItem(ACCESS_KEY, access)
      localStorage.setItem(REFRESH_KEY, refresh)
    } catch {
      /* 저장소를 쓸 수 없으면 이번 세션 동안만 로그인 상태가 유지되지 않는다 */
    }
  },
  /** 로그아웃·만료 시 두 토큰을 모두 지운다. */
  clear(): void {
    try {
      localStorage.removeItem(ACCESS_KEY)
      localStorage.removeItem(REFRESH_KEY)
    } catch {
      /* 무시 */
    }
  },
}

const baseURL = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({ baseURL, timeout: 15000 })

/** 모든 요청에 저장된 Access Token을 Bearer 헤더로 붙인다. */
api.interceptors.request.use((config) => {
  const token = tokenStore.getAccess()
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  return config
})

let refreshing: Promise<string> | null = null

/**
 * Refresh Token으로 새 토큰 쌍을 받는다.
 * 여러 요청이 동시에 401을 받아도 갱신 요청은 한 번만 보내고 결과를 함께 쓴다.
 */
function refreshTokens(): Promise<string> {
  if (!refreshing) {
    const refresh = tokenStore.getRefresh()
    const request = refresh
      ? axios.post(`${baseURL}/api/auth/token/refresh/`, { refresh }, { timeout: 15000 })
      : Promise.reject(new Error('Refresh Token이 없습니다.'))
    refreshing = request
      .then(({ data }) => {
        tokenStore.set(data.access, data.refresh)
        return data.access as string
      })
      .finally(() => {
        refreshing = null
      })
  }
  return refreshing
}

/** 로그인이 풀렸음을 앱에 알리고 저장된 토큰을 지운다. */
function expireSession(): void {
  tokenStore.clear()
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

type RetryConfig = InternalAxiosRequestConfig & { _retry?: boolean }

/** 401을 받으면 토큰을 한 번 갱신해 원래 요청을 다시 보내고, 갱신도 실패하면 세션을 만료시킨다. */
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as RetryConfig | undefined
    const isAuthPath = AUTH_PATHS.some((path) => config?.url?.includes(path))
    if (error.response?.status !== 401 || !config || isAuthPath) return Promise.reject(error)

    if (config._retry || !tokenStore.getRefresh()) {
      expireSession()
      return Promise.reject(error)
    }
    config._retry = true
    try {
      const access = await refreshTokens()
      config.headers.set('Authorization', `Bearer ${access}`)
      return api(config)
    } catch {
      expireSession()
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
