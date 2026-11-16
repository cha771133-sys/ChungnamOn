import type { AxiosRequestConfig } from 'axios'
import { api, tokenStore } from './api'

/** GET/PATCH /api/auth/me/ 응답 */
export type Profile = {
  id: number
  email: string
  language: string | null
  nationality: string | null
  visaType: string | null
  onboardingCompleted: boolean
}

export type ProfilePatch = Partial<Pick<Profile, 'language' | 'nationality' | 'visaType'>>

/** 이메일·비밀번호로 회원가입한다. 가입만 하고 토큰은 만들지 않는다. */
export async function register(email: string, password: string): Promise<void> {
  await api.post('/api/auth/register/', { email, password })
}

/** 로그인하고 받은 토큰 쌍을 저장한다. */
export async function login(email: string, password: string): Promise<void> {
  const { data } = await api.post('/api/auth/login/', { email, password })
  tokenStore.start(data.access, data.refresh)
}

/** 내 프로필과 온보딩 완료 여부를 불러온다. */
export async function fetchMe(): Promise<Profile> {
  const { data } = await api.get<Profile>('/api/auth/me/')
  return data
}

/** 프로필을 수정한다. 최초 설정 때는 세 필드를 모두 보내야 한다. */
export async function updateMe(patch: ProfilePatch): Promise<Profile> {
  const { data } = await api.patch<Profile>('/api/auth/me/', patch)
  return data
}

/** 서버의 Refresh Token을 무효화하고, 결과와 관계없이 저장된 토큰을 지운다. */
export async function logout(expectedSession?: number): Promise<void> {
  const session = tokenStore.session()
  // 로그아웃을 시작한 뒤(알림 해제 등을 기다리는 사이) 다른 계정으로 새로 로그인했으면 아무것도 하지 않는다
  if (expectedSession != null && expectedSession !== session) return
  const refresh = tokenStore.getRefresh()
  try {
    if (refresh) await api.post('/api/auth/logout/', { refresh }, { _session: session } as AxiosRequestConfig)
  } finally {
    // 로그아웃 요청 중에 다른 계정으로 새로 로그인했으면 그 세션은 지우지 않는다
    if (tokenStore.session() === session) tokenStore.clear()
  }
}

/** 저장된 토큰이 있어 로그인 상태로 볼 수 있는지 확인한다. */
export function hasSession(): boolean {
  return Boolean(tokenStore.getRefresh())
}
