// 서버 일정(/api/schedules/)을 화면이 쓰는 일정 모양으로 바꾼다.
import { localDocId, localToday } from './documentAdapter.js'

/** 앱 안에서 쓰는 일정 ID. 목업 일정(sc1 등)과 겹치지 않게 서버 ID 앞에 ss를 붙인다. */
export function localScheduleId(serverId) {
  return `ss${serverId}`
}

/** 앱 일정 ID에서 서버 일정 ID를 꺼낸다. 서버 일정이 아니면 null. */
export function serverScheduleId(localId) {
  const match = /^ss(\d+)$/.exec(String(localId))
  return match ? Number(match[1]) : null
}

/** 서버 일정 하나를 화면용 일정으로 바꾼다. 이 기기에만 있는 미리 알림 설정은 유지한다. */
export function scheduleFromServer(item, ownerId, prev) {
  return {
    ...prev,
    id: localScheduleId(item.scheduleId),
    serverId: item.scheduleId,
    source: 'api',
    ownerId,
    docId: localDocId(item.documentId),
    documentTitle: item.documentTitle,
    title: item.title,
    date: item.date,
    done: item.done,
    isOverdue: item.isOverdue,
    daysLeft: item.daysLeft,
    // 서버의 남은 일수는 받은 날에만 쓰고, 날이 바뀌면 기기에서 다시 계산한다
    fetchedOn: localToday(),
    agency: item.agency ?? null,
  }
}

/**
 * 서버 일정 목록을 로컬 일정에 합친다.
 * complete가 true(전체를 다 받음)이면 서버에 없는 이 계정의 서버 일정을 지운다.
 */
export function mergeServerSchedules(schedules, items, { ownerId, complete }) {
  const byId = new Map(schedules.map((s) => [s.id, s]))
  const serverIds = new Set()
  for (const item of items) {
    const id = localScheduleId(item.scheduleId)
    serverIds.add(id)
    byId.set(id, scheduleFromServer(item, ownerId, byId.get(id)))
  }
  let merged = [...byId.values()]
  if (complete) {
    merged = merged.filter((s) => s.source !== 'api' || s.ownerId !== ownerId || serverIds.has(s.id))
  }
  return merged
}
