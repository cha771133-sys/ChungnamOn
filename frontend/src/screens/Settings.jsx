import { useState } from 'react'
import { ChevronRight, Download, LogOut, RotateCcw, ShieldCheck, Server } from 'lucide-react'
import { api, parseApiError } from '../lib/api'
import { hasSession, logout as apiLogout, updateMe } from '../lib/auth'
import { useApp, purgeServerData } from '../store.js'
import { LANGS } from '../data/mock.js'
import { TopBar, TabBar, Toggle } from '../components/ui.jsx'
import { ProfileForm } from './Onboarding.jsx'
import { T } from '../i18n.js'

/** 설정 화면: 프로필(서버 저장), 안내 언어, 알림, 개인정보 안내, 로그아웃 */
export default function Settings() {
  const { data, update, openLang, setSheet, showToast, resetDemo, isStandalone, tab } = useApp()
  const L = LANGS.find((l) => l.code === data.profile.language)
  const [server, setServer] = useState('')
  const [saving, setSaving] = useState(false)

  /** 서버 상태 확인 (GET /api/health/) */
  const checkServer = async () => {
    setServer(T('확인 중...'))
    try {
      const { data: res } = await api.get('/api/health/')
      setServer(
        T('연결 성공: {v0}', {
          v0: res.status,
        }),
      )
    } catch (e) {
      setServer(
        T('연결 실패: {v0}', {
          v0: e.message,
        }),
      )
    }
  }

  /**
   * 프로필을 바로 화면에 반영하고 서버에 저장한다. 실패하면 이전 값으로 되돌린다.
   * 저장 중에는 폼을 잠가 요청이 겹치지 않게 하고, 국적·체류자격이 비어 있으면 보내지 않는다.
   */
  const setProfile = async (p) => {
    if (saving) return
    if (!p.nationality || !p.visa) {
      showToast(T('국적과 체류자격을 선택하세요'))
      return
    }
    const prev = data.profile
    update(() => ({ profile: p }))
    setSaving(true)
    try {
      await updateMe({ language: p.language, nationality: p.nationality, visaType: p.visa })
      showToast(T('저장했어요'))
    } catch (error) {
      update(() => ({ profile: prev }))
      const info = parseApiError(error)
      // 세션이 만료됐으면 앱이 로그인 화면으로 보내며 안내하므로 여기서는 알리지 않는다
      if (!hasSession()) return
      showToast(
        info.code === 'NETWORK_ERROR' || info.code === 'AUTHENTICATION_FAILED' || info.code === 'NOT_AUTHENTICATED'
          ? T('서버에 연결하지 못했어요. 다시 시도하세요')
          : T('저장하지 못했어요. 다시 시도하세요'),
      )
    } finally {
      setSaving(false)
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
    update((d) => ({ ...purgeServerData(d), user: null }))
    tab('login')
  }

  return (
    <>
      <TopBar title={T('설정')} />
      <main className="screen has-tabbar">
        <section className="settings-group">
          <h2>{T('내 정보')}</h2>
          {data.user?.email && <p className="settings-email">{data.user.email}</p>}
          <ProfileForm value={data.profile} onChange={setProfile} disabled={saving} lang={data.profile.language} />
        </section>

        <section className="settings-group">
          <h2>{T('안내 언어')}</h2>
          <button className="settings-row" onClick={openLang}>
            <span>{L?.label}</span>
            <small>{L?.ko}</small>
            <ChevronRight size={18} />
          </button>
        </section>

        <section className="settings-group">
          <h2>{T('알림')}</h2>
          <div className="toggle-row">
            <span>{T('마감 알림 받기')}</span>
            <Toggle checked={data.notify.enabled} onChange={setNotify} label={T('마감 알림 받기')} />
          </div>
          <div className={`toggle-row${data.notify.enabled ? '' : ' is-disabled'}`}>
            <span>{T('마감 7일 전')}</span>
            <Toggle checked={data.notify.d7} onChange={(v) => update((d) => ({ notify: { ...d.notify, d7: v } }))} label={T('기본 7일 전 알림')} />
          </div>
          <div className={`toggle-row${data.notify.enabled ? '' : ' is-disabled'}`}>
            <span>{T('마감 1일 전')}</span>
            <Toggle checked={data.notify.d1} onChange={(v) => update((d) => ({ notify: { ...d.notify, d1: v } }))} label={T('기본 1일 전 알림')} />
          </div>
          <p className="hint">{T('iPhone은 홈 화면에 추가한 뒤에만 알림을 받을 수 있어요.')}</p>
        </section>

        <section className="settings-group">
          <h2>{T('개인정보')}</h2>
          <div className="privacy-note">
            <ShieldCheck size={20} />
            <p>{T('올린 문서의 외국인등록번호와 주소는 자동으로 가려요. 원본 파일은 30일 뒤 자동 삭제되고, 저장된 정보는 암호화돼요.')}</p>
          </div>
        </section>

        <section className="settings-group">
          {!isStandalone && (
            <button className="settings-row" onClick={() => setSheet({ type: 'install' })}>
              <Download size={18} />
              <span>{T('홈 화면에 추가하기')}</span>
              <ChevronRight size={18} />
            </button>
          )}
          <button className="settings-row" onClick={checkServer}>
            <Server size={18} />
            <span>{T('서버 연결 확인')}</span>
            <small role="status">{server}</small>
          </button>
          <button className="settings-row" onClick={resetDemo}>
            <RotateCcw size={18} />
            <span>{T('데모 데이터 처음으로')}</span>
            <ChevronRight size={18} />
          </button>
          <button className="settings-row danger" onClick={logout}>
            <LogOut size={18} />
            <span>{T('로그아웃')}</span>
          </button>
        </section>
      </main>
      <TabBar active="settings" />
    </>
  )
}
