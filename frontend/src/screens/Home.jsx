import { Camera, BellOff, ChevronRight } from 'lucide-react'
import { useApp, ddayLabel, fmtDate, isVisibleDoc, sortDocs, isVisibleSchedule, scheduleDaysLeft } from '../store.js'
import { useServerSchedules } from '../lib/useServerSchedules.js'
import { useServerDocuments } from '../lib/useServerDocuments.js'
import { TopBar, TabBar } from '../components/ui.jsx'
import DocRow from './DocRow.jsx'
import { T } from '../i18n.js'

/** 홈: 다가오는 마감, 문서 올리기, 최근 문서 */
export default function Home() {
  const { data, nav, tab } = useApp()
  // 서버 목록 첫 페이지를 받아 최근 문서를 최신 상태로 맞춘다
  useServerDocuments()
  useServerSchedules()
  const open = data.schedules.filter((s) => !s.done && isVisibleSchedule(s, data.user))
  const upcoming = open.filter((s) => scheduleDaysLeft(s) >= 0).sort((a, b) => a.date.localeCompare(b.date))
  const overdue = open.filter((s) => scheduleDaysLeft(s) < 0)
  const next = upcoming[0]
  const n = next ? scheduleDaysLeft(next) : null

  return (
    <>
      <TopBar />
      <main className="screen has-tabbar">
        <h1 className="greeting">
          {T('안녕하세요')}
          {data.user?.email
            ? T(', {v0}님', {
                v0: data.user.email.split('@')[0],
              })
            : ''}
        </h1>

        {!data.notify.enabled && (
          <button className="banner tone-slate" onClick={() => tab('settings')}>
            <BellOff size={18} />
            <span>{T('알림이 꺼져 있어 마감일을 알려드릴 수 없어요.')}</span>
            <ChevronRight size={18} />
          </button>
        )}

        {next ? (
          <button className={`deadline-card${n <= 7 ? ' is-urgent' : ''}`} onClick={() => nav('scheduleDetail', { scheduleId: next.id })}>
            <span className="deadline-label">{T('다가오는 마감')}</span>
            <span className="deadline-dday">{ddayLabel(n)}</span>
            <strong className="deadline-title">{T(next.title)}</strong>
            <span className="deadline-date">
              {T('{v0}까지', {
                v0: fmtDate(next.date),
              })}
            </span>
            {overdue.length > 0 && (
              <span className="deadline-overdue">
                {T('기한이 지난 일정 {v0}개가 있어요', {
                  v0: overdue.length,
                })}
              </span>
            )}
          </button>
        ) : (
          <div className="deadline-card is-empty">
            <span className="deadline-label">{T('다가오는 마감')}</span>
            <strong className="deadline-title">{T('등록된 마감일이 없어요')}</strong>
          </div>
        )}

        <button className="upload-cta" onClick={() => nav('upload')}>
          <span className="upload-cta-icon">
            <Camera size={28} />
          </span>
          <span>
            <strong>{T('문서 올리기')}</strong>
            <small>{T('받은 고지서나 안내문을 사진 또는 PDF로 올려요')}</small>
          </span>
        </button>

        <section className="section">
          <div className="section-head">
            <h2>{T('최근 문서')}</h2>
            <button className="text-btn" onClick={() => tab('docs')}>
              {T('전체 보기')}
            </button>
          </div>
          <ul className="doc-list">
            {sortDocs(data.docs.filter((d) => isVisibleDoc(d, data.user)))
              .slice(0, 3)
              .map((d) => (
                <DocRow key={d.id} doc={d} />
              ))}
          </ul>
        </section>
      </main>
      <TabBar active="home" />
    </>
  )
}
