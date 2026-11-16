// 서버 마스킹(W3)이 넣는 가림 표시. 해석 결과의 번역문·요약에도 한국어 그대로 들어온다.
import { T } from '../i18n.js'

/** 가림 표시 이름 (한국어 원문, 화면에서 T()로 번역) */
export const MASK_LABELS = ['이름', '주소', '전화번호', '계좌', '여권번호']

/** 텍스트를 가림 표시와 나머지로 나누는 정규식. 목업 ●, 등록번호 등 *** 이상, 서버 [○○ 가림] */
export const MASK_SPLIT = /(●+|\*{3,}(?:-\*{3,})?|\[(?:이름|주소|전화번호|계좌|여권번호) 가림\])/

/** 조각이 가림 표시인지 */
export const isMask = (part) => /^(●|\*{3}|\[(?:이름|주소|전화번호|계좌|여권번호) 가림\]$)/.test(part)

/** 가림 표시를 화면에 보일 글자로 바꾼다. [이름 가림]은 안내 언어로 번역한다 (●·***는 그대로) */
export function maskText(part) {
  return part.startsWith('[') ? T(part) : part
}

/** 문장 속 [○○ 가림]을 안내 언어로 바꾼 문자열 (요약·챗봇 답변처럼 강조 없이 보여줄 때) */
export function localizeMasks(text) {
  if (typeof text !== 'string') return text
  return text.replace(/\[(?:이름|주소|전화번호|계좌|여권번호) 가림\]/g, (m) => T(m))
}
