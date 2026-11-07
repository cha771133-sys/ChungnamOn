import { useCallback, useEffect, useRef, useState } from 'react'
import { useApp } from '../store.js'
import { listDocuments } from './documents'
import { mergeServerList } from './documentAdapter.js'
import { parseApiError, tokenStore } from './api'
import { hasSession } from './auth'

/**
 * 서버의 내 문서 목록을 페이지 단위로 불러와 로컬 문서 목록에 합친다.
 * - 실패하면 같은 페이지를 다시 요청하도록 페이지를 넘기지 않는다 (retry).
 * - 전체 목록을 마지막 페이지까지 받으면, 받은 모든 페이지 기준으로 서버에서 사라진 문서를 정리한다.
 * @param {string|undefined} status 서버 상태 필터 (DONE, PROCESSING, FAILED). 없으면 전체
 */
export function useServerDocuments(status) {
  const { data, update, reverifyUser } = useApp()
  const [ids, setIds] = useState([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const seenIds = useRef([])
  const user = data.user
  // 현재 세션의 사용자로 확인된 뒤에만 서버 목록을 받는다 (다른 계정 문서가 섞이지 않게)
  const ready = hasSession() && user?.id != null && user.session === tokenStore.marker()

  useEffect(() => {
    if (!ready) return
    let alive = true
    const session = tokenStore.session()
    setLoading(true)
    setError('')
    listDocuments(page, status)
      .then((res) => {
        if (!alive || tokenStore.session() !== session) return
        const pageIds = res.results.map((item) => `s${item.documentId}`)
        const all = page === 1 ? pageIds : [...seenIds.current, ...pageIds.filter((id) => !seenIds.current.includes(id))]
        seenIds.current = all
        // 필터 없이 마지막 페이지까지 받았을 때만 정리한다 (앞 페이지 문서가 지워지지 않게 누적 ID 기준)
        const complete = !status && !res.next
        update((d) => ({ docs: mergeServerList(d.docs, res.results, { ownerId: user.id, complete, knownIds: all }) }))
        setIds(all)
        setHasMore(Boolean(res.next))
      })
      .catch((err) => {
        if (!alive) return
        const info = parseApiError(err)
        setError(info.code === 'NETWORK_ERROR' ? '서버에 연결하지 못했어요.' : '문서 목록을 불러오지 못했어요.')
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, user?.id, status, page, attempt])

  /** 다음 페이지. 직전 요청이 실패했거나 진행 중이면 넘기지 않는다. */
  const loadMore = useCallback(() => {
    if (loading || error || !hasMore) return
    setPage((p) => p + 1)
  }, [loading, error, hasMore])

  /** 실패한 같은 페이지를 다시 요청한다. 계정 확인 전이면 계정 확인부터 다시 한다. */
  const retry = useCallback(() => {
    if (!ready) reverifyUser()
    setAttempt((a) => a + 1)
  }, [ready, reverifyUser])

  return { ids, loading, error, hasMore, loadMore, retry, ready }
}
