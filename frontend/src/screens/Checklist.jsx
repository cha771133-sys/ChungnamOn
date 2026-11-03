import { useState } from 'react'
import { Check, Trash2, Plus, CalendarPlus, Building2, Pencil, CalendarCheck } from 'lucide-react'
import { useApp, dday, ddayLabel, fmtDate } from '../store.js'
import { TopBar, Badge, Empty } from '../components/ui.jsx'

export default function Checklist({ docId }) {
  const { data, updateDoc, nav, back, showToast } = useApp()
  const [edit, setEdit] = useState(false)
  const [text, setText] = useState('')
  const doc = data.docs.find((d) => d.id === docId)
  if (!doc) return null
  const items = doc.checklist
  const done = items.filter((i) => i.done).length
  const pct = items.length ? Math.round((done / items.length) * 100) : 0
  const schedule = data.schedules.find((s) => s.docId === docId && s.date === doc.deadline)

  const toggle = (id) => {
    updateDoc(docId, (d) => ({ checklist: d.checklist.map((i) => (i.id === id ? { ...i, done: !i.done } : i)) }))
    const item = items.find((i) => i.id === id)
    if (!item.done && done + 1 === items.length) showToast('모든 항목을 완료했어요')
  }
  const remove = (id) => updateDoc(docId, (d) => ({ checklist: d.checklist.filter((i) => i.id !== id) }))
  const add = (e) => {
    e.preventDefault()
    if (!text.trim()) return
    updateDoc(docId, (d) => ({
      checklist: [...d.checklist, { id: 'c' + Date.now(), text: text.trim(), state: 'CONFIRMED', done: false, custom: true, sources: [] }],
    }))
    setText('')
  }

  return (
    <>
      <TopBar
        title="할 일 체크리스트"
        onBack={back}
        right={
          items.length > 0 && (
            <button className="icon-btn" onClick={() => setEdit(!edit)} aria-label={edit ? '편집 마치기' : '항목 편집'}>
              {edit ? <Check size={22} /> : <Pencil size={20} />}
            </button>
          )
        }
      />
      <main className="screen has-actions">
        <p className="screen-sub">{doc.title}</p>

        {items.length === 0 ? (
          <Empty
            icon={Building2}
            title="체크리스트를 만들 정보가 부족해요"
            desc="문서와 공식 자료에서 절차를 충분히 찾지 못했어요. 담당기관에 확인하세요."
            action={
              <button className="btn btn-primary" onClick={() => nav('agency', { docId })}>
                담당기관 안내 보기
              </button>
            }
          />
        ) : (
          <>
            <div className="progress-card">
              <div className="progress-text">
                <strong>
                  {done}/{items.length} 완료
                </strong>
                <span>{pct}%</span>
              </div>
              <div className="progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${pct}%` }} />
              </div>
            </div>

            <ol className="check-list">
              {items.map((it, idx) => {
                const n = it.due ? dday(it.due) : null
                const overdue = !it.done && n !== null && n < 0
                return (
                  <li key={it.id} className={`check-item${it.done ? ' is-done' : ''}`}>
                    <span className="check-step" aria-hidden="true">{idx + 1}</span>
                    <button className="check-box" onClick={() => toggle(it.id)} aria-pressed={it.done} aria-label={`${it.text} ${it.done ? '완료 취소' : '완료로 표시'}`}>
                      {it.done && <Check size={16} strokeWidth={3} />}
                    </button>
                    <div className="check-body">
                      <div className="check-top">
                        <strong>{it.text}</strong>
                        {!it.custom && <Badge status={it.state} />}
                      </div>
                      {it.detail && <p>{it.detail}</p>}
                      {it.docs && <p className="check-meta">준비서류: {it.docs.join(', ')}</p>}
                      {it.method && <p className="check-meta">방법: {it.method}</p>}
                      {it.due && (
                        <p className={`check-meta${overdue ? ' is-overdue' : n !== null && n <= 7 ? ' is-soon' : ''}`}>
                          기한: {fmtDate(it.due)} ({overdue ? '기한 경과' : ddayLabel(n)})
                        </p>
                      )}
                      {it.note && (
                        <div className={`check-note tone-${it.state}`}>
                          <span>{it.note}</span>
                          <button className="text-btn" onClick={() => nav('agency', { docId })}>
                            담당기관 확인
                          </button>
                        </div>
                      )}
                    </div>
                    {edit && (
                      <button className="icon-btn danger" onClick={() => remove(it.id)} aria-label={`${it.text} 삭제`}>
                        <Trash2 size={18} />
                      </button>
                    )}
                  </li>
                )
              })}
            </ol>

            {edit && (
              <form className="add-row" onSubmit={add}>
                <input value={text} onChange={(e) => setText(e.target.value)} placeholder="직접 할 일 추가" aria-label="추가할 할 일" />
                <button className="btn btn-primary" type="submit">
                  <Plus size={18} /> 추가
                </button>
              </form>
            )}

            <p className="hint">완료 표시는 내 할 일 관리용이에요. 실제 납부나 접수가 끝났다는 뜻은 아니에요.</p>
          </>
        )}
      </main>

      <div className="bottom-actions" style={doc.deadline ? undefined : { gridTemplateColumns: '1fr' }}>
        {/* 유저 플로우 n36: 마감일이 있으면 일정 등록, 없으면 담당기관 안내만 */}
        <button className={`btn ${doc.deadline ? 'btn-outline' : 'btn-primary'}`} onClick={() => nav('agency', { docId })}>
          <Building2 size={18} /> 담당기관
        </button>
        {doc.deadline &&
          (schedule ? (
            <button className="btn btn-primary" onClick={() => nav('scheduleDetail', { scheduleId: schedule.id })}>
              <CalendarCheck size={18} /> 등록된 일정 보기
            </button>
          ) : (
            <button className="btn btn-primary" onClick={() => nav('scheduleNew', { docId })}>
              <CalendarPlus size={18} /> 마감일 일정 등록
            </button>
          ))}
      </div>
    </>
  )
}
