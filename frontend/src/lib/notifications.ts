import type { AxiosRequestConfig } from 'axios'
import { api } from './api'

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
