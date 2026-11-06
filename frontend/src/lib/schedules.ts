import axios from 'axios'
import { api } from './api'
import type { Page } from './documents'

/** /api/schedules/ 일정 응답. isOverdue·daysLeft는 서버가 한국 날짜 기준으로 계산한다. */
export type ScheduleItem = {
  scheduleId: number
  documentId: number
  documentTitle: string
  title: string
  date: string
  done: boolean
  doneAt: string | null
  isOverdue: boolean
  daysLeft: number
  agency: { name?: string; phone?: string } | null
  createdAt: string
  updatedAt: string
}

/** 같은 문서·날짜·업무명 일정이 이미 있을 때(409). 기존 일정 ID를 담는다. */
export class ScheduleExistsError extends Error {
  scheduleId: number
  constructor(scheduleId: number) {
    super('이미 등록된 일정입니다.')
    this.name = 'ScheduleExistsError'
    this.scheduleId = scheduleId
  }
}

/** 내 일정을 마감일 가까운 순으로 한 페이지 불러온다. */
export async function listSchedules(page = 1): Promise<Page<ScheduleItem>> {
  const { data } = await api.get<Page<ScheduleItem>>('/api/schedules/', { params: { page } })
  return data
}

/** 일정 하나를 불러온다. */
export async function getSchedule(scheduleId: number): Promise<ScheduleItem> {
  const { data } = await api.get<ScheduleItem>(`/api/schedules/${scheduleId}/`)
  return data
}

/**
 * 해석 결과의 마감일(deadlines) 중 하나로 일정을 등록한다. title을 비우면 서버가 "라벨 - 문서 제목"으로 만든다.
 * 이미 같은 일정이 있으면 ScheduleExistsError를 던진다.
 */
export async function createSchedule(documentId: number, date: string, title = ''): Promise<ScheduleItem> {
  try {
    const { data } = await api.post<ScheduleItem>('/api/schedules/', { documentId, date, title })
    return data
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 409) {
      const existing = (err.response.data as { scheduleId?: number })?.scheduleId
      if (typeof existing === 'number') throw new ScheduleExistsError(existing)
    }
    throw err
  }
}

/** 일정의 완료 여부를 바꾼다. */
export async function updateSchedule(scheduleId: number, done: boolean): Promise<ScheduleItem> {
  const { data } = await api.patch<ScheduleItem>(`/api/schedules/${scheduleId}/`, { done })
  return data
}

/** 일정을 삭제한다. */
export async function deleteSchedule(scheduleId: number): Promise<void> {
  await api.delete(`/api/schedules/${scheduleId}/`)
}
