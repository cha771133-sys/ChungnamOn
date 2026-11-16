import type { AxiosRequestConfig } from 'axios'
import { api, StaleSessionError, tokenStore } from './api'

/** 마감 알림 수신 설정. 켜져 있으면 서버가 미완료 일정의 마감 7일 전과 1일 전에 알림을 보낸다. */
export type NotifyPreference = {
  enabled: boolean
  /** 알림을 받을 수 있게 등록된 이 계정의 기기 수 (Firebase 연동 전에는 0) */
  registeredDeviceCount: number
}

/** 지금 계정의 알림 수신 설정을 불러온다. 한 번도 정하지 않았으면 꺼짐(false)이다. */
export async function getNotifyPreference(): Promise<NotifyPreference> {
  const { data } = await api.get<NotifyPreference>('/api/notifications/preferences/')
  return data
}

/**
 * 알림 수신을 켜거나 끈다.
 * session을 넘기면 그 로그인 세션이 아직 유효할 때만 보낸다 (계정이 바뀐 뒤 다른 계정 설정이 바뀌는 것 방지).
 */
export async function setNotifyPreference(enabled: boolean, session?: number): Promise<NotifyPreference> {
  const config = { _session: session } as AxiosRequestConfig
  const { data } = await api.patch<NotifyPreference>('/api/notifications/preferences/', { enabled }, config)
  return data
}

/*
 * 화면 상태 동기화
 * - 알림 설정을 바꿀 때마다 변경 번호를 올린다. 그 전에 보낸 조회(GET) 응답이 늦게 와도 새 값을 덮어쓰지 않는다.
 * - 요청을 보낸 로그인 세션이 바뀌었으면(로그아웃·계정 전환) 응답을 화면에 반영하지 않는다.
 */
let changeSeq = 0

/** 서버의 알림 설정을 불러와, 그사이 설정 변경·계정 전환이 없었을 때만 apply로 넘긴다. */
export async function refreshNotifyPreference(apply: (pref: NotifyPreference) => void): Promise<void> {
  const session = tokenStore.session()
  const seq = changeSeq
  const pref = await getNotifyPreference()
  if (tokenStore.session() !== session || seq !== changeSeq) return
  apply(pref)
}

/**
 * 알림 설정을 저장한다. 성공하면 onSaved, 실패하면 onFailed를 부른다.
 * 저장 중에 더 새로운 변경이 생겼거나 계정이 바뀌었으면 둘 다 부르지 않는다 (지난 결과로 화면을 바꾸지 않음).
 */
export async function saveNotifyPreference(enabled: boolean, handlers: { onSaved: (pref: NotifyPreference) => void; onFailed: (err: unknown) => void }): Promise<void> {
  const seq = ++changeSeq
  const session = tokenStore.session()
  const isCurrent = () => tokenStore.session() === session && seq === changeSeq
  try {
    const pref = await setNotifyPreference(enabled, session)
    if (isCurrent()) handlers.onSaved(pref)
  } catch (err) {
    if (err instanceof StaleSessionError || !isCurrent()) return
    handlers.onFailed(err)
  }
}
