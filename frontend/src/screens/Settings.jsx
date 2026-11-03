import { useState } from 'react'
import { ChevronRight, Download, LogOut, RotateCcw, ShieldCheck, Server } from 'lucide-react'
import { api } from '../lib/api'
import { useApp } from '../store.js'
import { LANGS } from '../data/mock.js'
import { TopBar, TabBar, Toggle } from '../components/ui.jsx'
import { ProfileForm } from './Onboarding.jsx'

export default function Settings() {
  const { data, update, openLang, setSheet, showToast, resetDemo, isStandalone, tab } = useApp()
  const L = LANGS.find((l) => l.code === data.profile.language)
  const [server, setServer] = useState('')

  const checkServer = async () => {
    setServer('확인 중...')
    try {
      const { data: res } = await api.get('/api/health/')
      setServer(`연결 성공: ${res.status}`)
    } catch (e) {
      setServer(`연결 실패: ${e.message}`)
    }
  }

  const setProfile = (p) => {
    update(() => ({ profile: p }))
    showToast('저장했어요')
  }
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
  const logout = () => {
    update(() => ({ user: null, onboarded: false }))
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
