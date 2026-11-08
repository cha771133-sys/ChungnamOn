import { ChevronLeft, Globe, Home, FileText, CalendarDays, Settings, Info, Check, AlertTriangle, HelpCircle, GitBranch, X } from 'lucide-react'
import { useApp } from '../store.js'
import { LANGS } from '../data/mock.js'
import { T } from '../i18n.js'

export const STATUS = {
  MATCHED: { label: '일치', tone: 'green', Icon: Check },
  MISMATCH: { label: '불일치', tone: 'amber', Icon: AlertTriangle },
  UNVERIFIED: { label: '미확인', tone: 'slate', Icon: HelpCircle },
  CONFIRMED: { label: '확인됨', tone: 'green', Icon: Check },
  CONDITIONAL: { label: '조건부', tone: 'violet', Icon: GitBranch },
  NEEDS_CHECK: { label: '확인 필요', tone: 'slate', Icon: HelpCircle },
}

export function Badge({ status, children }) {
  const s = STATUS[status]
  const I = s.Icon
  return (
    <span className={`badge tone-${s.tone}`}>
      <I size={13} strokeWidth={2.6} aria-hidden="true" />
      {children || T(s.label)}
    </span>
  )
}

/** 도장(인장) 모양의 검증 표시. 이 서비스의 시그니처 요소. long이면 긴 번역 문구도 원 안에 들어가게 글자를 줄인다. */
export function Stamp({ tone, top, bottom, small, long }) {
  return (
    <div className={`stamp tone-${tone}${small ? ' stamp-sm' : ''}${long ? ' stamp-long' : ''}`} role="img" aria-label={`${top} ${bottom}`}>
      <span className="stamp-top">{top}</span>
      <span className="stamp-bottom">{bottom}</span>
    </div>
  )
}

export function TopBar({ title, onBack, right }) {
  const { openLang, lang } = useApp()
  const L = LANGS.find((l) => l.code === lang)
  return (
    <header className="topbar">
      <div className="topbar-left">
        {onBack ? (
          <button className="icon-btn" onClick={onBack} aria-label={T('뒤로 가기')}>
            <ChevronLeft size={24} />
          </button>
        ) : title ? null : (
          <span className="brand">
            <span className="brand-mark" aria-hidden="true" />
            ChungnamOn
          </span>
        )}
        {title && <h1 className="topbar-title">{title}</h1>}
      </div>
      <div className="topbar-right">
        {right}
        <button className="lang-btn" onClick={openLang} aria-label={T('언어 변경')}>
          <Globe size={15} />
          <span>{L?.label}</span>
        </button>
      </div>
    </header>
  )
}

const TABS = [
  ['home', '홈', Home],
  ['docs', '문서', FileText],
  ['schedule', '일정', CalendarDays],
  ['settings', '설정', Settings],
]

export function TabBar({ active }) {
  const { tab } = useApp()
  return (
    <nav className="tabbar" aria-label={T('주요 메뉴')}>
      {TABS.map(([key, label, Icon]) => (
        <button key={key} className={`tab${active === key ? ' is-active' : ''}`} onClick={() => tab(key)} aria-current={active === key ? 'page' : undefined}>
          <Icon size={22} strokeWidth={active === key ? 2.4 : 1.8} />
          <span>{T(label)}</span>
        </button>
      ))}
    </nav>
  )
}

export function Disclaimer({ children }) {
  return (
    <p className="disclaimer">
      <Info size={16} aria-hidden="true" />
      <span>{children || T('AI 결과는 참고용이에요. 최종 자격, 기한, 제출 방법은 담당기관에서 확인하세요.')}</span>
    </p>
  )
}

export function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`switch${checked ? ' is-on' : ''}`} onClick={() => onChange(!checked)} disabled={disabled}>
      <span />
    </button>
  )
}

export function Sheet({ title, onClose, children }) {
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={T('닫기')}>
            <X size={22} />
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  )
}

export function Empty({ icon: Icon, title, desc, action }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={28} />
      </div>
      <strong>{title}</strong>
      {desc && <p>{desc}</p>}
      {action}
    </div>
  )
}
