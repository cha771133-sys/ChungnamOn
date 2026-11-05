import { useState } from 'react'
import { Bell, BellOff, ChevronRight, CalendarDays, List, FileText, ListChecks, Building2, Trash2, AlertTriangle, Check } from 'lucide-react'
import { useApp, dday, ddayLabel, fmtDate, pick, TODAY } from '../store.js'
import { TopBar, TabBar, Toggle, Empty } from '../components/ui.jsx'

function ScheduleCard({ s }) {
  const { data, nav } = useApp()
  const doc = data.docs.find((d) => d.id === s.docId)
  const n = dday(s.date)
  const cls = s.done ? 'is-done' : n < 0 ? 'is-overdue' : n <= 7 ? 'is-soon' : ''
  return (
    <li>
      <button className={`sched-card ${cls}`} onClick={() => nav('scheduleDetail', { scheduleId: s.id })}>
        <span className="sched-dday">{s.done ? '완료' : ddayLabel(n)}</span>
        <span className="sched-main">
          <strong>{s.title}</strong>
          <span>
            {fmtDate(s.date)}
            {doc ? `, ${doc.title}` : ''}
          </span>
        </span>
        <ChevronRight size={18} className="chev" />
      </button>
    </li>
  )
}

function Month({ schedules }) {
  const [sel, setSel] = useState(null)
  const y = TODAY.getFullYear()
  const m = TODAY.getMonth()
  const first = new Date(y, m, 1).getDay()
  const days = new Date(y, m + 1, 0).getDate()
  const key = (d) => `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
  const cells = [...Array(first).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  const selected = sel ? schedules.filter((s) => s.date === key(sel)) : []
  return (
    <div className="month">
      <h2 className="month-title">
        {y}년 {m + 1}월
      </h2>
      <div className="month-grid" role="grid">
        {['일', '월', '화', '수', '목', '금', '토'].map((w) => (
          <span key={w} className="month-week">
            {w}
          </span>
        ))}
        {cells.map((d, i) => {
          if (!d) return <span key={`e${i}`} />
          const items = schedules.filter((s) => s.date === key(d))
          const isToday = d === TODAY.getDate()
          return (
            <button
              key={d}
              className={`month-day${isToday ? ' is-today' : ''}${sel === d ? ' is-selected' : ''}${items.some((s) => !s.done) ? ' has-item' : ''}`}
              onClick={() => setSel(d)}
              aria-label={`${m + 1}월 ${d}일${items.length ? `, 일정 ${items.length}개` : ''}`}
            >
              {d}
              {items.length > 0 && <i />}
            </button>
          )
        })}
      </div>
      {sel && (
        <div className="month-sel">
          {selected.length ? (
            <ul className="sched-list">
              {selected.map((s) => (
                <ScheduleCard key={s.id} s={s} />
              ))}
            </ul>
          ) : (
            <p className="hint">
              {m + 1}월 {sel}일에는 일정이 없어요.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export function Schedule() {
  const { data, tab, nav } = useApp()
  const [mode, setMode] = useState('list')
  const s = data.schedules
  const overdue = s.filter((x) => !x.done && dday(x.date) < 0)
  const upcoming = s.filter((x) => !x.done && dday(x.date) >= 0).sort((a, b) => a.date.localeCompare(b.date))
  const done = s.filter((x) => x.done)

  return (
    <>
      <TopBar
        title="일정"
        right={
          <>
            <button className="icon-btn" onClick={() => tab('settings')} aria-label="알림 수신 설정">
              {data.notify.enabled ? <Bell size={20} /> : <BellOff size={20} />}
            </button>
            <button className="icon-btn" onClick={() => setMode(mode === 'list' ? 'month' : 'list')} aria-label={mode === 'list' ? '달력으로 보기' : '목록으로 보기'}>
              {mode === 'list' ? <CalendarDays size={21} /> : <List size={21} />}
            </button>
          </>
        }
      />
      <main className="screen has-tabbar">
        {!data.notify.enabled && (
          <button className="banner tone-slate" onClick={() => tab('settings')}>
            <BellOff size={18} />
            <span>알림이 꺼져 있어요. 설정에서 켜면 마감 7일 전과 1일 전에 알려드려요.</span>
            <ChevronRight size={18} />
          </button>
        )}

        {s.length === 0 ? (
          <Empty
            icon={CalendarDays}
            title="등록된 일정이 없어요"
            desc="문서 해석 결과의 체크리스트에서 ‘마감일 일정 등록’을 누르면 여기에 모여요."
            action={
              <button className="btn btn-primary" onClick={() => tab('docs')}>
                내 문서 보기
              </button>
            }
          />
        ) : mode === 'month' ? (
          <Month schedules={s} />
        ) : (
          <>
            {overdue.length > 0 && (
              <section className="section">
                <h2 className="list-head is-overdue">기한이 지난 일정</h2>
                <ul className="sched-list">
                  {overdue.map((x) => (
                    <ScheduleCard key={x.id} s={x} />
                  ))}
                </ul>
              </section>
            )}
            <section className="section">
              <h2 className="list-head">다가오는 마감</h2>
              {upcoming.length ? (
                <ul className="sched-list">
                  {upcoming.map((x) => (
                    <ScheduleCard key={x.id} s={x} />
                  ))}
                </ul>
              ) : (
                <p className="hint">다가오는 마감이 없어요.</p>
              )}
            </section>
            {done.length > 0 && (
              <section className="section">
                <h2 className="list-head">완료</h2>
                <ul className="sched-list">
                  {done.map((x) => (
                    <ScheduleCard key={x.id} s={x} />
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        {upcoming[0] && (
          <section className="section">
            <h2 className="list-head">알림은 이렇게 와요</h2>
            <button className="push-preview is-inline" onClick={() => nav('scheduleDetail', { scheduleId: upcoming[0].id })}>
              <div className="push-app">
                <span className="push-icon" />
                ChungnamOn <small>오전 9:00</small>
              </div>
              <strong>{upcoming[0].title} 마감 7일 전이에요</strong>
              <span>{fmtDate(upcoming[0].date)}까지예요. 눌러서 할 일을 확인하세요.</span>
            </button>
          </section>
        )}
      </main>
      <TabBar active="schedule" />
    </>
  )
}

export function ScheduleNew({ docId }) {
  const { data, update, back, replace, tab, nav, showToast } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  const [title, setTitle] = useState(doc ? pick(doc.task, 'ko') : '')
  const [remind, setRemind] = useState({ d7: true, d1: true })
  if (!doc) return null
  const n = doc.deadline ? dday(doc.deadline) : null

  const save = () => {
    const exist = data.schedules.find((s) => s.docId === docId && s.date === doc.deadline)
    if (exist) {
      showToast('이미 등록된 일정이에요')
      replace('scheduleDetail', { scheduleId: exist.id })
      return
    }
    const s = { id: 'sc' + Date.now(), docId, title: title.trim() || doc.title, date: doc.deadline, done: false, remind }
    update((d) => ({ schedules: [...d.schedules, s] }))
    showToast('일정을 등록했어요')
    tab('schedule') // 유저 플로우 n35 → n37
  }

  return (
    <>
      <TopBar title="마감일 일정 등록" onBack={back} />
      <main className="screen">
        {!doc.deadline ? (
          <Empty
            icon={AlertTriangle}
            title="마감일을 확인하지 못했어요"
            desc="문서에서 마감일을 찾지 못해 일정으로 등록할 수 없어요. 담당기관에 기한을 확인하세요."
            action={
              <button className="btn btn-primary" onClick={() => nav('agency', { docId })}>
                담당기관 안내 보기
              </button>
            }
          />
        ) : (
          <>
            <label className="field">
              <span className="field-label">할 일</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} />
            </label>
            <div className="field">
              <span className="field-label">마감일 (문서에서 확인한 날짜)</span>
              <div className="readonly">
                {fmtDate(doc.deadline)} <span className={n < 0 ? 'is-overdue' : ''}>{ddayLabel(n)}</span>
              </div>
            </div>
            {n < 0 && <p className="callout tone-amber">이미 기한이 지났어요. 등록할 수는 있지만 담당기관에 먼저 확인하세요.</p>}
            <div className="field">
              <span className="field-label">미리 알림</span>
              <div className="toggle-row">
                <span>마감 7일 전</span>
                <Toggle checked={remind.d7} onChange={(v) => setRemind({ ...remind, d7: v })} label="마감 7일 전 알림" />
              </div>
              <div className="toggle-row">
                <span>마감 1일 전</span>
                <Toggle checked={remind.d1} onChange={(v) => setRemind({ ...remind, d1: v })} label="마감 1일 전 알림" />
              </div>
            </div>
            <div className="linked">
              <span className="field-label">함께 연결돼요</span>
              <p>
                <FileText size={16} /> {doc.title}
              </p>
              <p>
                <ListChecks size={16} /> 할 일 체크리스트 {doc.checklist.length}개
              </p>
              <p>
                <Building2 size={16} /> {doc.agency.name}
              </p>
            </div>
            <p className="hint">일정 등록은 실제 납부나 신청을 뜻하지 않아요.</p>
            <div className="sticky-actions">
              <button className="btn btn-primary btn-block btn-lg" onClick={save}>
                일정 등록하기
              </button>
            </div>
          </>
        )}
      </main>
    </>
  )
}

export function ScheduleDetail({ scheduleId }) {
  const { data, update, back, nav, showToast } = useApp()
  const s = data.schedules.find((x) => x.id === scheduleId)
  if (!s) return null
  const doc = data.docs.find((d) => d.id === s.docId)
  const n = dday(s.date)
  const overdue = !s.done && n < 0
  const set = (patch) => update((d) => ({ schedules: d.schedules.map((x) => (x.id === s.id ? { ...x, ...patch } : x)) }))
  const remove = () => {
    update((d) => ({ schedules: d.schedules.filter((x) => x.id !== s.id) }))
    showToast('일정을 삭제했어요')
    back()
  }

  return (
    <>
      <TopBar
        title="일정 상세"
        onBack={back}
        right={
          <button className="icon-btn danger" onClick={remove} aria-label="일정 삭제">
            <Trash2 size={20} />
          </button>
        }
      />
      <main className="screen">
        <header className={`sched-head${overdue ? ' is-overdue' : !s.done && n <= 7 ? ' is-soon' : ''}`}>
          <span className="sched-head-dday">{s.done ? '완료' : ddayLabel(n)}</span>
          <h1 className="h2">{s.title}</h1>
          <p>{fmtDate(s.date)}까지</p>
        </header>

        {overdue && <p className="callout tone-seal">기한이 지났어요. 지금 낼 수 있는지, 추가 금액이 있는지 담당기관에 확인하세요.</p>}

        <button className={`done-toggle${s.done ? ' is-done' : ''}`} onClick={() => set({ done: !s.done })} aria-pressed={s.done}>
          <span className="check-box">{s.done && <Check size={16} strokeWidth={3} />}</span>
          {s.done ? '완료로 표시했어요' : '완료로 표시하기'}
        </button>

        {doc && (
          <section className="section">
            <h2 className="list-head">연결된 정보</h2>
            <ul className="link-list">
              <li>
                <button onClick={() => nav('result', { docId: doc.id })}>
                  <FileText size={18} /> <span>{doc.title}</span> <ChevronRight size={18} />
                </button>
              </li>
              <li>
                <button onClick={() => nav('checklist', { docId: doc.id })}>
                  <ListChecks size={18} />
                  <span>
                    체크리스트 {doc.checklist.filter((c) => c.done).length}/{doc.checklist.length} 완료
                  </span>
                  <ChevronRight size={18} />
                </button>
              </li>
              <li>
                <button onClick={() => nav('agency', { docId: doc.id })}>
                  <Building2 size={18} /> <span>{doc.agency.name}</span> <ChevronRight size={18} />
                </button>
              </li>
            </ul>
          </section>
        )}

        <section className="section">
          <h2 className="list-head">미리 알림</h2>
          <div className="toggle-row">
            <span>마감 7일 전</span>
            <Toggle checked={s.remind.d7} onChange={(v) => set({ remind: { ...s.remind, d7: v } })} label="마감 7일 전 알림" />
          </div>
          <div className="toggle-row">
            <span>마감 1일 전</span>
            <Toggle checked={s.remind.d1} onChange={(v) => set({ remind: { ...s.remind, d1: v } })} label="마감 1일 전 알림" />
          </div>
          {!data.notify.enabled && <p className="hint">앱 알림이 꺼져 있어 알림을 받을 수 없어요. 설정에서 켜세요.</p>}
        </section>
      </main>
    </>
  )
}
