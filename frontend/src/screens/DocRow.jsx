import { ChevronRight, Loader2, AlertCircle } from 'lucide-react'
import { useApp, openDoc, fmtShort, needsCheck } from '../store.js'

export default function DocRow({ doc }) {
  const { nav, showToast } = useApp()
  let status
  if (doc.status === 'processing')
    status = (
      <span className="doc-status is-processing">
        <Loader2 size={14} className="spin" /> 분석 중
      </span>
    )
  else if (doc.status === 'failed')
    status = (
      <span className="doc-status is-failed">
        <AlertCircle size={14} /> 다시 올려야 해요
      </span>
    )
  else if (needsCheck(doc)) status = <span className="doc-status is-check">확인 필요 항목 있음</span>
  else status = <span className="doc-status is-done">해석 완료</span>

  return (
    <li>
      <button className="doc-row" onClick={() => openDoc(doc, nav, showToast)}>
        <span className={`doc-thumb type-${doc.status}`} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="doc-main">
          <span className="doc-type">{doc.docType}</span>
          <strong>{doc.title}</strong>
          <span className="doc-meta">
            {fmtShort(doc.uploadedAt)} 올림 {status}
          </span>
        </span>
        <ChevronRight size={20} className="chev" />
      </button>
    </li>
  )
}
