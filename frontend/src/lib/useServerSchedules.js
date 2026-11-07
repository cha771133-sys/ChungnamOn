import { useCallback, useEffect, useState } from 'react'
import { useApp } from '../store.js'
import { listSchedules } from './schedules'
import { mergeServerSchedules } from './scheduleAdapter.js'
import { parseApiError, tokenStore } from './api'
import { hasSession } from './auth'
import { T } from '../i18n.js'

// 일정은 문서보다 훨씬 적으므로 전부 받아 화면에서 나눠 보여준다 (최대 페이지 수 제한)
const MAX_PAGES = 10

/** 서버의 내 일정을 모두 불러와 로컬 일정에 합친다. */
export function useServerSchedules() {
  const { data, update, reverifyUser } = useApp()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const user = data.user
  const ready = hasSession() && user?.id != null && user.session === tokenStore.marker()

  useEffect(() => {
    if (!ready) return
    let alive = true
    const session = tokenStore.session()
    setLoading(true)
    setError('')
    ;(async () => {
      const items = []
      let page = 1
      let more = true
      while (more && page <= MAX_PAGES) {
        const res = await listSchedules(page)
        items.push(...res.results)
        more = Boolean(res.next)
        page += 1
      }
      return { items, complete: !more }
    })()
      .then(({ items, complete }) => {
        if (!alive || tokenStore.session() !== session) return
        update((d) => ({ schedules: mergeServerSchedules(d.schedules, items, { ownerId: user.id, complete }) }))
      })
      .catch((err) => {
        if (!alive) return
        const info = parseApiError(err)
        setError(info.code === 'NETWORK_ERROR' ? T('서버에 연결하지 못했어요.') : T('일정을 불러오지 못했어요.'))
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, user?.id, attempt])

  /** 다시 불러온다. 계정 확인 전이면 계정 확인부터 다시 한다. */
  const retry = useCallback(() => {
    if (!ready) reverifyUser()
    setAttempt((a) => a + 1)
  }, [ready, reverifyUser])

  return { loading, error, retry, ready }
}
