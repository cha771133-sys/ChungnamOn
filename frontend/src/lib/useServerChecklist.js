import { useCallback, useEffect, useRef, useState } from 'react'
import { useApp } from '../store.js'
import { getChecklist, setChecklistDone } from './checklist'
import { adaptChecklist, adaptChecklistItem } from './checklistAdapter.js'
import { parseApiError, StaleSessionError, tokenStore } from './api'
import { T } from '../i18n.js'

/**
 * 서버 문서의 체크리스트를 불러오고 완료 체크를 저장한다.
 * - 목록은 화면에 들어올 때 받아 문서에 저장한다 (담당기관 화면·오프라인에서도 보이도록)
 * - 체크하면 화면에 바로 반영하고, 실패하면 되돌린다
 * - 문서를 다시 처리해 항목 ID가 바뀌었으면(404) 목록을 다시 받는다
 * - 요청 중 계정이 바뀌었거나, 목록 요청 뒤 체크가 있었으면 늦게 온 목록으로 덮어쓰지 않는다
 * @param {object|null} doc 화면 문서 (시연용 목업 문서면 아무것도 하지 않는다)
 */
export function useServerChecklist(doc) {
  const { updateDoc, showToast } = useApp()
  const serverId = doc?.source === 'api' && doc.status === 'done' && !doc.lowConfidence ? doc.serverId : null
  const docId = doc?.id
  const [status, setStatus] = useState(serverId != null ? 'loading' : 'idle') // idle | loading | ready | error
  const [pending, setPending] = useState(() => new Set())
  const changeSeq = useRef(0)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  /**
   * 목록을 받아 문서에 저장한다.
   * @returns {Promise<'applied'|'discarded'|'failed'>}
   *   applied: 화면에 반영 / discarded: 받았지만 그사이 화면 이탈·계정 전환·새 체크가 있어 버림 / failed: 받지 못함
   */
  const load = useCallback(() => {
    if (serverId == null) return Promise.resolve('discarded')
    const session = tokenStore.session()
    const seq = changeSeq.current
    return getChecklist(serverId)
      .then((raw) => {
        if (!alive.current || tokenStore.session() !== session || seq !== changeSeq.current) return 'discarded'
        updateDoc(docId, (d) => ({ checklist: adaptChecklist(raw, d.sourceKeyOf) }))
        setStatus('ready')
        return 'applied'
      })
      .catch(() => {
        if (alive.current) setStatus((s) => (s === 'ready' ? s : 'error'))
        return 'failed'
      })
  }, [serverId, docId, updateDoc])

  useEffect(() => {
    load()
    // 화면에 들어올 때 한 번 받는다
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId])

  /** 다시 불러오기 (오류 화면의 버튼용) */
  const retry = () => {
    setStatus('loading')
    load()
  }

  /** 항목 완료 여부를 바꾼다. 같은 항목을 저장하는 중이면 무시한다. */
  const toggle = async (item) => {
    if (serverId == null || item.serverItemId == null || pending.has(item.id)) return
    const next = !item.done
    changeSeq.current += 1
    const session = tokenStore.session()
    const setItem = (patch) => updateDoc(docId, (d) => ({ checklist: d.checklist.map((i) => (i.id === item.id ? { ...i, ...patch } : i)) }))
    setItem({ done: next })
    setPending((p) => new Set(p).add(item.id))
    try {
      const saved = await setChecklistDone(serverId, item.serverItemId, next, session)
      if (tokenStore.session() !== session) return
      updateDoc(docId, (d) => ({ checklist: d.checklist.map((i) => (i.id === item.id ? adaptChecklistItem(saved, d.sourceKeyOf) : i)) }))
      return true
    } catch (err) {
      if (err instanceof StaleSessionError || tokenStore.session() !== session) return
      if (parseApiError(err).status === 404) {
        // 문서를 다시 처리해 항목이 바뀌었다. 새 목록을 받아 바꾼다
        changeSeq.current += 1
        const result = await load()
        // 기다리는 사이 화면을 나갔거나 계정이 바뀌었으면 아무것도 바꾸지 않는다 (다시 들어오면 새로 받는다)
        if (!alive.current || tokenStore.session() !== session) return
        if (result === 'applied') {
          showToast(T('체크리스트가 바뀌어 다시 불러왔어요'))
          return
        }
        // 새 목록을 반영하지 못했으면(실패 또는 그사이 다른 체크로 버림) 이 항목의 체크를 되돌린다
        setItem({ done: item.done })
        showToast(result === 'failed' ? T('체크리스트를 불러오지 못했어요. 다시 시도하세요') : T('체크리스트가 바뀌었어요. 다시 시도하세요'))
        return
      }
      setItem({ done: item.done })
      showToast(T('저장하지 못했어요. 다시 시도하세요'))
    } finally {
      if (alive.current)
        setPending((p) => {
          const n = new Set(p)
          n.delete(item.id)
          return n
        })
    }
  }

  return { isServer: serverId != null, status, retry, toggle, pending }
}
