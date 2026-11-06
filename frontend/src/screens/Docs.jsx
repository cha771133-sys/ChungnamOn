import { useState } from 'react'
import { FileText, Plus, RefreshCw } from 'lucide-react'
import { useApp, isVisibleDoc } from '../store.js'
import { TopBar, TabBar, Empty } from '../components/ui.jsx'
import { useServerDocuments } from '../lib/useServerDocuments.js'
import DocRow from './DocRow.jsx'

// 서버 상태 필터 (PR #44 목록 API의 status)
const FILTERS = [
  ['all', '전체', undefined],
  ['done', '완료', 'DONE'],
  ['processing', '분석 중', 'PROCESSING'],
  ['failed', '실패', 'FAILED'],
]

/** 내 문서함: 서버 목록(최신순, 20개씩)과 시연용 예시 문서를 보여준다. */
export default function Docs() {
  const { nav } = useApp()
  const [filter, setFilter] = useState('all')
  const [refreshKey, setRefreshKey] = useState(0)
  const status = FILTERS.find(([k]) => k === filter)[2]

  return (
    <>
      <TopBar
        title="내 문서"
        right={
          <>
            <button className="icon-btn" onClick={() => setRefreshKey((k) => k + 1)} aria-label="새로고침">
              <RefreshCw size={20} />
            </button>
            <button className="icon-btn" onClick={() => nav('upload')} aria-label="문서 올리기">
              <Plus size={22} />
            </button>
          </>
        }
      />
      <main className="screen has-tabbar">
        <div className="chips" role="tablist">
          {FILTERS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={filter === k} className={`chip${filter === k ? ' is-active' : ''}`} onClick={() => setFilter(k)}>
              {label}
            </button>
          ))}
        </div>
        {/* 필터·새로고침이 바뀌면 목록 상태(페이지 등)를 처음부터 다시 받는다 */}
        <DocList key={`${filter}-${refreshKey}`} status={status} showDemo={filter === 'all'} />
      </main>
      <TabBar active="docs" />
    </>
  )
}

/** 한 필터의 서버 문서 목록 + (전체일 때) 시연용 예시 문서 */
function DocList({ status, showDemo }) {
  const { data, nav } = useApp()
  const { ids, loading, error, hasMore, loadMore, retry, ready } = useServerDocuments(status)
  const serverDocs = ids
    .map((id) => data.docs.find((d) => d.id === id))
    .filter((d) => d && isVisibleDoc(d, data.user))
  const demoDocs = showDemo ? data.docs.filter((d) => d.source !== 'api') : []

  return (
    <>
      {error && (
        <button className="banner tone-amber" onClick={retry}>
          <RefreshCw size={18} />
          <span>{error} 눌러서 다시 시도하세요.</span>
        </button>
      )}

      {serverDocs.length > 0 ? (
        <ul className="doc-list">
          {serverDocs.map((d) => (
            <DocRow key={d.id} doc={d} />
          ))}
        </ul>
      ) : loading || !ready ? (
        <p className="hint list-loading">문서 목록을 불러오는 중이에요...</p>
      ) : (
        !error && (
          <Empty
            icon={FileText}
            title={status ? '해당하는 문서가 없어요' : '아직 올린 문서가 없어요'}
            desc={status ? undefined : '받은 고지서나 안내문을 올리면 내 언어로 풀어 드려요.'}
            action={
              !status && (
                <button className="btn btn-primary" onClick={() => nav('upload')}>
                  문서 올리기
                </button>
              )
            }
          />
        )
      )}

      {hasMore && !error && (
        <button className="btn btn-outline btn-block load-more" onClick={loadMore} disabled={loading}>
          {loading ? '불러오는 중...' : '더 보기'}
        </button>
      )}

      {demoDocs.length > 0 && (
        <section className="section">
          <h2 className="list-head">시연용 예시 문서</h2>
          <ul className="doc-list">
            {demoDocs.map((d) => (
              <DocRow key={d.id} doc={d} />
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
