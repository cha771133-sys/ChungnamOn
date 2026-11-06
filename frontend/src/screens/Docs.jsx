import { useState } from 'react'
import { FileText, Plus } from 'lucide-react'
import { useApp, needsCheck, isVisibleDoc } from '../store.js'
import { TopBar, TabBar, Empty } from '../components/ui.jsx'
import DocRow from './DocRow.jsx'

const FILTERS = [
  ['all', '전체'],
  ['processing', '분석 중'],
  ['done', '완료'],
  ['check', '확인 필요'],
]

export default function Docs() {
  const { data, nav } = useApp()
  const [f, setF] = useState('all')
  const docs = data.docs.filter((d) => isVisibleDoc(d, data.user))
  const list = docs.filter((d) => {
    if (f === 'all') return true
    if (f === 'processing') return d.status === 'processing'
    if (f === 'done') return d.status === 'done'
    return needsCheck(d)
  })
  return (
    <>
      <TopBar
        title="내 문서"
        right={
          <button className="icon-btn" onClick={() => nav('upload')} aria-label="문서 올리기">
            <Plus size={22} />
          </button>
        }
      />
      <main className="screen has-tabbar">
        <div className="chips" role="tablist">
          {FILTERS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={f === k} className={`chip${f === k ? ' is-active' : ''}`} onClick={() => setF(k)}>
              {label}
            </button>
          ))}
        </div>
        {docs.length === 0 ? (
          <Empty
            icon={FileText}
            title="아직 올린 문서가 없어요"
            desc="받은 고지서나 안내문을 올리면 내 언어로 풀어 드려요."
            action={
              <button className="btn btn-primary" onClick={() => nav('upload')}>
                문서 올리기
              </button>
            }
          />
        ) : list.length === 0 ? (
          <Empty icon={FileText} title="해당하는 문서가 없어요" />
        ) : (
          <ul className="doc-list">
            {list.map((d) => (
              <DocRow key={d.id} doc={d} />
            ))}
          </ul>
        )}
      </main>
      <TabBar active="docs" />
    </>
  )
}
