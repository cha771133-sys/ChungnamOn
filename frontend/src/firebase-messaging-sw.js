// 기존 다국어 표시·클릭·PWA 처리기를 SDK보다 먼저 등록한다.
import '../public/sw.js'
import { initializeApp } from '@firebase/app'
import { getMessaging } from '@firebase/messaging/sw'

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
}

if (config.apiKey && config.projectId && config.messagingSenderId && config.appId) {
  // 창과 같은 앱 이름·설정으로 FID/구독 갱신 상태를 공유한다.
  // 알림 표시는 sw.js가 전담하며, SDK는 pushsubscriptionchange도 처리한다.
  getMessaging(initializeApp(config, 'chungnamon'))
}
