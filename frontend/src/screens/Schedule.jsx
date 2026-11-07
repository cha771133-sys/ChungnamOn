import { useEffect, useState } from 'react'
import { Bell, BellOff, ChevronRight, CalendarDays, List, FileText, ListChecks, Building2, Trash2, AlertTriangle, Check, RefreshCw } from 'lucide-react'
import { useApp, dday, ddayLabel, fmtDate, pick, startOfToday, isVisibleSchedule, scheduleDaysLeft, openDoc } from '../store.js'
import { TopBar, TabBar, Toggle, Empty } from '../components/ui.jsx'
import { useServerSchedules } from '../lib/useServerSchedules.js'
import { createSchedule, deleteSchedule, getSchedule, updateSchedule, ScheduleExistsError } from '../lib/schedules'
import { localScheduleId, mergeServerSchedules, serverScheduleId } from '../lib/scheduleAdapter.js'
import { isCalendarDate } from '../lib/documentAdapter.js'
import { parseApiError } from '../lib/api'
import { T } from '../i18n.js'

/** 일정 카드 (목록·달력 공통) */
function ScheduleCard({ s }) {
  const { data, nav } = useApp()
  const doc = data.docs.find((d) => d.id === s.docId)
  const n = scheduleDaysLeft(s)
  const docTitle = doc?.title ?? s.documentTitle
  const cls = s.done ? 'is-done' : n < 0 ? 'is-overdue' : n <= 7 ? 'is-soon' : ''
  return (
    <li>
      <button className={`sched-card ${cls}`} onClick={() => nav('scheduleDetail', { scheduleId: s.id })}>
        <span className="sched-dday">{s.done ? T('완료') : ddayLabel(n)}</span>
        <span className="sched-main">
          <strong>{T(s.title)}</strong>
          <span>
            {fmtDate(s.date)}
            {docTitle ? `, ${docTitle}` : ''}
          </span>
        </span>
        <ChevronRight size={18} className="chev" />
      </button>
    </li>
  )
}

/** 이번 달 달력 */
function Month({ schedules }) {
  const [sel, setSel] = useState(null)
  const today = startOfToday()
  const y = today.getFullYear()
  const m = today.getMonth()
  const first = new Date(y, m, 1).getDay()
  const days = new Date(y, m + 1, 0).getDate()
  const key = (d) => `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
  const cells = [...Array(first).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  const selected = sel ? schedules.filter((s) => s.date === key(sel)) : []
  return (
    <div className="month">
      <h2 className="month-title">
        {T('{v0}년 {v1}월', {
          v0: y,
          v1: m + 1,
        })}
      </h2>
      <div className="month-grid" role="grid">
        {[T('일'), T('월'), T('화'), T('수'), T('목'), T('금'), T('토')].map((w) => (
          <span key={w} className="month-week">
            {w}
          </span>
        ))}
        {cells.map((d, i) => {
          if (!d) return <span key={`e${i}`} />
          const items = schedules.filter((s) => s.date === key(d))
          const isToday = d === today.getDate()
          return (
            <button
              key={d}
              className={`month-day${isToday ? ' is-today' : ''}${sel === d ? ' is-selected' : ''}${items.some((s) => !s.done) ? ' has-item' : ''}`}
              onClick={() => setSel(d)}
              aria-label={T('{v0}월 {v1}일{v2}', {
                v0: m + 1,
                v1: d,
                v2: items.length ? T(', 일정 {v0}개', { v0: items.length }) : '',
              })}
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
              {T('{v0}월 {v1}일에는 일정이 없어요.', {
                v0: m + 1,
                v1: sel,
              })}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** 일정 탭: 서버 일정(마감 가까운 순)과 시연용 일정을 기한 경과·다가오는 마감·완료로 나눠 보여준다. */
export function Schedule() {
  const { data, tab, nav } = useApp()
  const [mode, setMode] = useState('list')
  const { error, retry, loading } = useServerSchedules()
  const s = data.schedules.filter((x) => isVisibleSchedule(x, data.user))
  const overdue = s.filter((x) => !x.done && scheduleDaysLeft(x) < 0)
  const upcoming = s.filter((x) => !x.done && scheduleDaysLeft(x) >= 0).sort((a, b) => a.date.localeCompare(b.date))
  const done = s.filter((x) => x.done)

  return (
    <>
      <TopBar
        title={T('일정')}
        right={
          <>
            <button className="icon-btn" onClick={retry} aria-label={T('새로고침')} disabled={loading}>
              <RefreshCw size={20} />
            </button>
            <button className="icon-btn" onClick={() => tab('settings')} aria-label={T('알림 수신 설정')}>
              {data.notify.enabled ? <Bell size={20} /> : <BellOff size={20} />}
            </button>
            <button className="icon-btn" onClick={() => setMode(mode === 'list' ? 'month' : 'list')} aria-label={mode === 'list' ? T('달력으로 보기') : T('목록으로 보기')}>
              {mode === 'list' ? <CalendarDays size={21} /> : <List size={21} />}
            </button>
          </>
        }
      />
      <main className="screen has-tabbar">
        {error && (
          <button className="banner tone-amber" onClick={retry}>
            <RefreshCw size={18} />
            <span>
              {T('{v0} 눌러서 다시 시도하세요.', {
                v0: T(error),
              })}
            </span>
          </button>
        )}
        {!data.notify.enabled && (
          <button className="banner tone-slate" onClick={() => tab('settings')}>
            <BellOff size={18} />
            <span>{T('알림이 꺼져 있어요. 설정에서 켜면 마감 7일 전과 1일 전에 알려드려요.')}</span>
            <ChevronRight size={18} />
          </button>
        )}

        {s.length === 0 ? (
          <Empty
            icon={CalendarDays}
            title={T('등록된 일정이 없어요')}
            desc={T('문서 해석 결과에서 ‘마감일 일정 등록’을 누르면 여기에 모여요.')}
            action={
              <button className="btn btn-primary" onClick={() => tab('docs')}>
                {T('내 문서 보기')}
              </button>
            }
          />
        ) : mode === 'month' ? (
          <Month schedules={s} />
        ) : (
          <>
            {overdue.length > 0 && (
              <section className="section">
                <h2 className="list-head is-overdue">{T('기한이 지난 일정')}</h2>
                <ul className="sched-list">
                  {overdue.map((x) => (
                    <ScheduleCard key={x.id} s={x} />
                  ))}
                </ul>
              </section>
            )}
            <section className="section">
              <h2 className="list-head">{T('다가오는 마감')}</h2>
              {upcoming.length ? (
                <ul className="sched-list">
                  {upcoming.map((x) => (
                    <ScheduleCard key={x.id} s={x} />
                  ))}
                </ul>
              ) : (
                <p className="hint">{T('다가오는 마감이 없어요.')}</p>
              )}
            </section>
            {done.length > 0 && (
              <section className="section">
                <h2 className="list-head">{T('완료')}</h2>
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
            <h2 className="list-head">{T('알림은 이렇게 와요')}</h2>
            <button className="push-preview is-inline" onClick={() => nav('scheduleDetail', { scheduleId: upcoming[0].id })}>
              <div className="push-app">
                <span className="push-icon" />
                ChungnamOn <small>{T('오전 9:00')}</small>
              </div>
              <strong>
                {T('{v0} 마감 7일 전이에요', {
                  v0: T(upcoming[0].title),
                })}
              </strong>
              <span>
                {T('{v0}까지예요. 눌러서 할 일을 확인하세요.', {
                  v0: fmtDate(upcoming[0].date),
                })}
              </span>
            </button>
          </section>
        )}
      </main>
      <TabBar active="schedule" />
    </>
  )
}

/** 일정 등록: 서버 문서는 해석 결과의 마감일 중 하나를 골라 서버에 등록, 시연용 문서는 로컬에 등록 */
/** 같은 날짜가 여러 번 오면 첫 항목만 남긴다 (서버 find_deadline_label도 첫 항목의 업무명을 쓴다) */
function firstPerDate(list) {
  const seen = new Set()
  return list.filter((x) => (seen.has(x.date) ? false : seen.add(x.date)))
}

export function ScheduleNew({ docId }) {
  const { data, update, back, replace, tab, nav, showToast } = useApp()
  const doc = data.docs.find((d) => d.id === docId)
  const isServer = doc?.source === 'api'
  // 서버 문서는 마감일이 여러 개일 수 있다 (같은 날짜는 하나로)
  // 서버가 준 순서와 상관없이 날짜순으로 보여주고, 가장 가까운 마감일을 기본으로 고른다
  const options = isServer
    ? firstPerDate((doc.deadlines ?? []).filter((x) => isCalendarDate(x?.date))).sort((a, b) => a.date.localeCompare(b.date))
    : doc?.deadline
      ? [{ label: '', date: doc.deadline }]
      : []
  // 이미 등록한 마감일 (지금 계정에 보이는 일정 기준). 기본 선택은 아직 등록하지 않은 가장 가까운 마감일
  const registeredDates = new Set(data.schedules.filter((s) => s.docId === docId && isVisibleSchedule(s, data.user)).map((s) => s.date))
  const openOptions = options.filter((o) => !registeredDates.has(o.date))
  // 오늘 이후 가장 가까운 날짜, 모두 지났으면 가장 최근에 지난 날짜 (대표 마감일과 같은 규칙)
  const pickDefault = (list) => list.find((o) => dday(o.date) >= 0) ?? list[list.length - 1]
  const firstUpcoming = pickDefault(openOptions) ?? pickDefault(options)
  const [date, setDate] = useState(firstUpcoming?.date ?? null)
  const [title, setTitle] = useState(doc && !isServer ? pick(doc.task, 'ko') || doc.title : '')
  const [remind, setRemind] = useState({ d7: true, d1: true })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!doc) return null
  const n = date ? dday(date) : null

  const saveLocal = () => {
    const exist = data.schedules.find((s) => s.docId === docId && s.date === date)
    if (exist) {
      showToast(T('이미 등록된 일정이에요'))
      replace('scheduleDetail', { scheduleId: exist.id })
      return
    }
    const s = { id: 'sc' + Date.now(), docId, title: title.trim() || doc.title, date, done: false, remind }
    update((d) => ({ schedules: [...d.schedules, s] }))
    showToast(T('일정을 등록했어요'))
    tab('schedule') // 유저 플로우 n35 → n37
  }

  const saveServer = async () => {
    setBusy(true)
    setError('')
    try {
      const item = await createSchedule(doc.serverId, date, title.trim())
      update((d) => ({
        schedules: mergeServerSchedules(d.schedules, [item], { ownerId: data.user?.id, complete: false }).map((s) => (s.id === localScheduleId(item.scheduleId) ? { ...s, remind } : s)),
      }))
      showToast(T('일정을 등록했어요'))
      tab('schedule')
    } catch (err) {
      if (err instanceof ScheduleExistsError) {
        showToast(T('이미 등록된 일정이에요'))
        replace('scheduleDetail', { scheduleId: localScheduleId(err.scheduleId) })
        return
      }
      const info = parseApiError(err)
      setError(info.code === 'NETWORK_ERROR' ? '서버에 연결하지 못했어요. 다시 시도하세요.' : info.fields.date || info.fields.documentId || info.message || '일정을 등록하지 못했어요.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <TopBar title={T('마감일 일정 등록')} onBack={back} />
      <main className="screen">
        {!options.length ? (
          <Empty
            icon={AlertTriangle}
            title={T('마감일을 확인하지 못했어요')}
            desc={T('문서에서 마감일을 찾지 못해 일정으로 등록할 수 없어요. 담당기관에 기한을 확인하세요.')}
            action={
              <button className="btn btn-primary" onClick={() => nav('agency', { docId })}>
                {T('담당기관 안내 보기')}
              </button>
            }
          />
        ) : (
          <>
            <label className="field">
              <span className="field-label">{T('할 일')}</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={isServer ? T('비워 두면 "마감 항목 - 문서 제목"으로 등록돼요') : ''} />
            </label>
            <div className="field">
              <span className="field-label">{T('마감일 (문서에서 확인한 날짜)')}</span>
              {options.length > 1 ? (
                <div className="option-list" role="radiogroup" aria-label={T('마감일 선택')}>
                  {options.map((o) => (
                    <button key={o.date} type="button" role="radio" aria-checked={date === o.date} className={`option${date === o.date ? ' is-selected' : ''}`} onClick={() => setDate(o.date)}>
                      <span className="option-text">
                        <strong>{fmtDate(o.date)}</strong>
                        <small>
                          {o.label ? `${o.label}, ` : ''}
                          {ddayLabel(dday(o.date))}
                          {registeredDates.has(o.date) ? T(', 등록됨') : ''}
                        </small>
                      </span>
                      {date === o.date && <Check size={20} />}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="readonly">
                  {fmtDate(date)} <span className={n < 0 ? 'is-overdue' : ''}>{ddayLabel(n)}</span>
                </div>
              )}
            </div>
            {n < 0 && <p className="callout tone-amber">{T('이미 기한이 지났어요. 등록할 수는 있지만 담당기관에 먼저 확인하세요.')}</p>}
            <div className="field">
              <span className="field-label">{T('미리 알림')}</span>
              <div className="toggle-row">
                <span>{T('마감 7일 전')}</span>
                <Toggle checked={remind.d7} onChange={(v) => setRemind({ ...remind, d7: v })} label={T('마감 7일 전 알림')} />
              </div>
              <div className="toggle-row">
                <span>{T('마감 1일 전')}</span>
                <Toggle checked={remind.d1} onChange={(v) => setRemind({ ...remind, d1: v })} label={T('마감 1일 전 알림')} />
              </div>
            </div>
            <div className="linked">
              <span className="field-label">{T('함께 연결돼요')}</span>
              <p>
                <FileText size={16} /> {doc.title}
              </p>
              {doc.checklist?.length > 0 && (
                <p>
                  <ListChecks size={16} />{' '}
                  {T('할 일 체크리스트 {v0}개', {
                    v0: doc.checklist.length,
                  })}
                </p>
              )}
              <p>
                <Building2 size={16} /> {doc.agency?.name ?? T('확인되지 않음')}
              </p>
            </div>
            {error && (
              <p className="form-error" role="alert">
                {T(error)}
              </p>
            )}
            <p className="hint">{T('일정 등록은 실제 납부나 신청을 뜻하지 않아요.')}</p>
            <div className="sticky-actions">
              <button className="btn btn-primary btn-block btn-lg" onClick={isServer ? saveServer : saveLocal} disabled={busy || !date}>
                {busy ? T('등록하는 중...') : T('일정 등록하기')}
              </button>
            </div>
          </>
        )}
      </main>
    </>
  )
}

/** 일정 상세: 완료 표시·삭제는 서버 일정이면 서버에 반영한다. 이 기기에 없는 서버 일정은 불러온다. */
export function ScheduleDetail({ scheduleId }) {
  const { data, update, back, nav, showToast } = useApp()
  const s = data.schedules.find((x) => x.id === scheduleId)
  const serverId = serverScheduleId(scheduleId)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState('')

  // 이 기기에 없는 서버 일정(이미 등록된 일정으로 이동한 경우 등)은 서버에서 불러온다
  useEffect(() => {
    if (s || serverId == null) return
    getSchedule(serverId)
      .then((item) => update((d) => ({ schedules: mergeServerSchedules(d.schedules, [item], { ownerId: d.user?.id, complete: false }) })))
      .catch(() => setLoadError('일정을 불러오지 못했어요.'))
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [scheduleId])

  if (!s) {
    return (
      <>
        <TopBar title={T('일정 상세')} onBack={back} />
        <main className="screen center-screen">
          <p className="lead">{T(loadError || '일정을 불러오는 중이에요...')}</p>
        </main>
      </>
    )
  }
  if (!isVisibleSchedule(s, data.user)) return null

  const doc = data.docs.find((d) => d.id === s.docId)
  const n = scheduleDaysLeft(s)
  const overdue = !s.done && n < 0
  const isServer = s.source === 'api'
  const set = (patch) => update((d) => ({ schedules: d.schedules.map((x) => (x.id === s.id ? { ...x, ...patch } : x)) }))

  /** 완료 표시: 화면에 먼저 반영하고 서버 저장이 실패하면 되돌린다. */
  const toggleDone = async () => {
    const next = !s.done
    set({ done: next })
    if (!isServer) return
    setBusy(true)
    try {
      const item = await updateSchedule(s.serverId, next)
      update((d) => ({ schedules: mergeServerSchedules(d.schedules, [item], { ownerId: s.ownerId, complete: false }) }))
    } catch {
      set({ done: !next })
      showToast(T('저장하지 못했어요. 다시 시도하세요'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (isServer) {
      setBusy(true)
      try {
        await deleteSchedule(s.serverId)
      } catch (err) {
        // 이미 지워진 일정(404)이 아니면 실패로 알린다
        if (parseApiError(err).status !== 404) {
          setBusy(false)
          showToast(T('삭제하지 못했어요. 다시 시도하세요'))
          return
        }
      }
    }
    update((d) => ({ schedules: d.schedules.filter((x) => x.id !== s.id) }))
    showToast(T('일정을 삭제했어요'))
    back()
  }

  /** 연결된 문서 열기. 이 기기에 없는 서버 문서는 결과를 불러와서 연다. */
  const openLinkedDoc = () => {
    if (doc) openDoc(doc, nav, showToast)
    else nav('analyzing', { docId: s.docId, reopen: true })
  }

  return (
    <>
      <TopBar
        title={T('일정 상세')}
        onBack={back}
        right={
          <button className="icon-btn danger" onClick={remove} aria-label={T('일정 삭제')} disabled={busy}>
            <Trash2 size={20} />
          </button>
        }
      />
      <main className="screen">
        <header className={`sched-head${overdue ? ' is-overdue' : !s.done && n <= 7 ? ' is-soon' : ''}`}>
          <span className="sched-head-dday">{s.done ? T('완료') : ddayLabel(n)}</span>
          <h1 className="h2">{T(s.title)}</h1>
          <p>
            {T('{v0}까지', {
              v0: fmtDate(s.date),
            })}
          </p>
        </header>

        {overdue && <p className="callout tone-seal">{T('기한이 지났어요. 지금 낼 수 있는지, 추가 금액이 있는지 담당기관에 확인하세요.')}</p>}

        <button className={`done-toggle${s.done ? ' is-done' : ''}`} onClick={toggleDone} aria-pressed={s.done} disabled={busy}>
          <span className="check-box">{s.done && <Check size={16} strokeWidth={3} />}</span>
          {s.done ? T('완료로 표시했어요') : T('완료로 표시하기')}
        </button>

        <section className="section">
          <h2 className="list-head">{T('연결된 정보')}</h2>
          <ul className="link-list">
            <li>
              <button onClick={openLinkedDoc}>
                <FileText size={18} /> <span>{doc?.title ?? s.documentTitle ?? T('연결된 문서')}</span> <ChevronRight size={18} />
              </button>
            </li>
            {doc?.checklist?.length > 0 && (
              <li>
                <button onClick={() => nav('checklist', { docId: doc.id })}>
                  <ListChecks size={18} />
                  <span>
                    {T('체크리스트 {v0}/{v1} 완료', {
                      v0: doc.checklist.filter((c) => c.done).length,
                      v1: doc.checklist.length,
                    })}
                  </span>
                  <ChevronRight size={18} />
                </button>
              </li>
            )}
            {doc ? (
              <li>
                <button onClick={() => nav('agency', { docId: doc.id })}>
                  <Building2 size={18} /> <span>{doc.agency?.name ?? T('담당기관')}</span> <ChevronRight size={18} />
                </button>
              </li>
            ) : (
              s.agency?.name && (
                <li>
                  <span className="link-static">
                    <Building2 size={18} /> <span>{s.agency.name}</span>
                  </span>
                </li>
              )
            )}
          </ul>
        </section>

        <section className="section">
          <h2 className="list-head">{T('미리 알림')}</h2>
          <div className="toggle-row">
            <span>{T('마감 7일 전')}</span>
            <Toggle checked={s.remind?.d7 ?? true} onChange={(v) => set({ remind: { ...s.remind, d7: v } })} label={T('마감 7일 전 알림')} />
          </div>
          <div className="toggle-row">
            <span>{T('마감 1일 전')}</span>
            <Toggle checked={s.remind?.d1 ?? true} onChange={(v) => set({ remind: { ...s.remind, d1: v } })} label={T('마감 1일 전 알림')} />
          </div>
          {!data.notify.enabled && <p className="hint">{T('앱 알림이 꺼져 있어 알림을 받을 수 없어요. 설정에서 켜세요.')}</p>}
          {isServer && <p className="hint">{T('미리 알림 설정은 이 기기에 저장돼요.')}</p>}
        </section>
      </main>
    </>
  )
}
