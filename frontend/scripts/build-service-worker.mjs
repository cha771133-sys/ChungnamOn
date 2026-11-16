import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const root = fileURLToPath(new URL('../', import.meta.url))

// SDK와 공개 환경변수를 하나의 classic worker로 묶어 도메인 루트에 제공한다.
await build({
  root,
  configFile: false,
  publicDir: false,
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    target: 'es2023',
    lib: {
      entry: fileURLToPath(new URL('../src/firebase-messaging-sw.js', import.meta.url)),
      name: 'ChungnamOnServiceWorker',
      formats: ['iife'],
      fileName: () => 'firebase-messaging-sw.js',
    },
  },
})
