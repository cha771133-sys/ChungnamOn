import { useCallback, useEffect, useState } from 'react'
import { useApp } from '../store.js'
import { listDocuments } from './documents'
import { mergeServerList } from './documentAdapter.js'
import { parseApiError, tokenStore } from './api'
import { hasSession } from './auth'

/**
 * 서버의 내 문서 목록을 페이지 단위로 불러와 로컬 문서 목록에 합친다.
 * @param {string|undefined} status 서버 상태 필터 (DONE, PROCESSING, FAILED). 없으면 전체
 * @returns {{ ids: string[], loading: boolean, error: string, hasMore: boolean, loadMore: () => void, reload: () => void }}
 *   ids: 서버가 돌려준 순서대로의 문서 ID 목록 (화면은 이 순서로 그린다)
 */
export function useServerDocuments(status) {
  const { data, update } = useApp()
  const [ids, setIds] = useState([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
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
        const complete = !status && page === 1 && !res.next
        update((d) => ({ docs: mergeServerList(d.docs, res.results, { ownerId: user.id, complete }) }))
        const pageIds = res.results.map((item) => `s${item.documentId}`)
        setIds((prev) => (page === 1 ? pageIds : [...prev, ...pageIds.filter((id) => !prev.includes(id))]))
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
  }, [ready, user?.id, status, page, version])

  const loadMore = useCallback(() => setPage((p) => p + 1), [])
  const reload = useCallback(() => {
    setPage(1)
    setVersion((v) => v + 1)
  }, [])

  return { ids, loading, error, hasMore, loadMore, reload, ready }
}
