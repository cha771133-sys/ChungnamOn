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

  const load = useCallback(() => {
    if (serverId == null) return Promise.resolve()
    const session = tokenStore.session()
    const seq = changeSeq.current
    return getChecklist(serverId)
      .then((raw) => {
        if (!alive.current || tokenStore.session() !== session || seq !== changeSeq.current) return
        updateDoc(docId, (d) => ({ checklist: adaptChecklist(raw, d.sourceKeyOf) }))
        setStatus('ready')
      })
      .catch(() => {
        if (alive.current) setStatus((s) => (s === 'ready' ? s : 'error'))
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
        // 문서를 다시 처리해 항목이 바뀌었다
        showToast(T('체크리스트가 바뀌어 다시 불러왔어요'))
        changeSeq.current += 1
        load()
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
