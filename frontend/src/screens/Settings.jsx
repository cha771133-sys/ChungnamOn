import { useState } from 'react'
import { ChevronRight, Download, LogOut, RotateCcw, ShieldCheck, Server } from 'lucide-react'
import { api, parseApiError } from '../lib/api'
import { logout as apiLogout, updateMe } from '../lib/auth'
import { useApp } from '../store.js'
import { LANGS } from '../data/mock.js'
import { TopBar, TabBar, Toggle } from '../components/ui.jsx'
import { ProfileForm } from './Onboarding.jsx'

/** 설정 화면: 프로필(서버 저장), 안내 언어, 알림, 개인정보 안내, 로그아웃 */
export default function Settings() {
  const { data, update, openLang, setSheet, showToast, resetDemo, isStandalone, tab } = useApp()
  const L = LANGS.find((l) => l.code === data.profile.language)
  const [server, setServer] = useState('')

  /** 서버 상태 확인 (GET /api/health/) */
  const checkServer = async () => {
    setServer('확인 중...')
    try {
      const { data: res } = await api.get('/api/health/')
      setServer(`연결 성공: ${res.status}`)
    } catch (e) {
      setServer(`연결 실패: ${e.message}`)
    }
  }

  /** 프로필을 바로 화면에 반영하고 서버에 저장한다. 실패하면 이전 값으로 되돌린다. */
  const setProfile = async (p) => {
    const prev = data.profile
    update(() => ({ profile: p }))
    try {
      await updateMe({ language: p.language, nationality: p.nationality, visaType: p.visa })
      showToast('저장했어요')
    } catch (error) {
      update(() => ({ profile: prev }))
      const info = parseApiError(error)
      // 로그인 만료는 앱이 로그인 화면으로 보내며 안내하므로 여기서는 알리지 않는다
      if (info.code === 'AUTHENTICATION_FAILED' || info.code === 'NOT_AUTHENTICATED') return
      showToast(info.code === 'NETWORK_ERROR' ? '서버에 연결하지 못했어요' : '저장하지 못했어요. 다시 시도하세요')
    }
  }
  /** 알림 켜기·끄기. 켤 때 브라우저 권한을 아직 묻지 않았으면 요청한다. */
  const setNotify = async (enabled) => {
    if (enabled && 'Notification' in window && Notification.permission === 'default') {
      try {
        await Notification.requestPermission()
      } catch {
        /* 무시 */
      }
    }
    update((d) => ({ notify: { ...d.notify, enabled } }))
  }
  /** 서버의 Refresh Token을 무효화하고 로그인 화면으로 이동한다. */
  const logout = async () => {
    try {
      await apiLogout()
    } catch {
      /* 서버 요청이 실패해도 저장된 토큰은 이미 지웠다 */
    }
    update(() => ({ user: null }))
    tab('login')
  }

  return (
    <>
      <TopBar title="설정" />
      <main className="screen has-tabbar">
        <section className="settings-group">
          <h2>내 정보</h2>
          {data.user?.email && <p className="settings-email">{data.user.email}</p>}
          <ProfileForm value={data.profile} onChange={setProfile} />
        </section>

        <section className="settings-group">
          <h2>안내 언어</h2>
          <button className="settings-row" onClick={openLang}>
            <span>{L?.label}</span>
            <small>{L?.ko}</small>
            <ChevronRight size={18} />
          </button>
        </section>

        <section className="settings-group">
          <h2>알림</h2>
          <div className="toggle-row">
            <span>마감 알림 받기</span>
            <Toggle checked={data.notify.enabled} onChange={setNotify} label="마감 알림 받기" />
          </div>
          <div className={`toggle-row${data.notify.enabled ? '' : ' is-disabled'}`}>
            <span>마감 7일 전</span>
            <Toggle checked={data.notify.d7} onChange={(v) => update((d) => ({ notify: { ...d.notify, d7: v } }))} label="기본 7일 전 알림" />
          </div>
          <div className={`toggle-row${data.notify.enabled ? '' : ' is-disabled'}`}>
            <span>마감 1일 전</span>
            <Toggle checked={data.notify.d1} onChange={(v) => update((d) => ({ notify: { ...d.notify, d1: v } }))} label="기본 1일 전 알림" />
          </div>
          <p className="hint">iPhone은 홈 화면에 추가한 뒤에만 알림을 받을 수 있어요.</p>
        </section>

        <section className="settings-group">
          <h2>개인정보</h2>
          <div className="privacy-note">
            <ShieldCheck size={20} />
            <p>올린 문서의 외국인등록번호와 주소는 자동으로 가려요. 원본 파일은 30일 뒤 자동 삭제되고, 저장된 정보는 암호화돼요.</p>
          </div>
        </section>

        <section className="settings-group">
          {!isStandalone && (
            <button className="settings-row" onClick={() => setSheet({ type: 'install' })}>
              <Download size={18} />
              <span>홈 화면에 추가하기</span>
              <ChevronRight size={18} />
            </button>
          )}
          <button className="settings-row" onClick={checkServer}>
            <Server size={18} />
            <span>서버 연결 확인</span>
            <small role="status">{server}</small>
          </button>
          <button className="settings-row" onClick={resetDemo}>
            <RotateCcw size={18} />
            <span>데모 데이터 처음으로</span>
            <ChevronRight size={18} />
          </button>
          <button className="settings-row danger" onClick={logout}>
            <LogOut size={18} />
            <span>로그아웃</span>
          </button>
        </section>
      </main>
      <TabBar active="settings" />
    </>
  )
}
