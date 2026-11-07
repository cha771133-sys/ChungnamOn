// 담당기관 조회(/api/sources/agencies/lookup/) 결과를 화면에서 쓰기 쉽게 정리한다.
import { lookupAgency } from './agencies'
import { fmtDate, docDeadline } from '../store.js'

/**
 * 문서에 적힌 기관명으로 공식 담당기관을 찾는다.
 * 서버는 정확히 같은 이름만 찾으므로, "아산시청 세무과"처럼 부서까지 적힌 이름을 못 찾으면
 * 앞의 기관 이름("아산시청")으로 한 번 더 찾는다. 이때는 부서가 다를 수 있어 byOrgName으로 표시한다.
 */
export async function findOfficialAgency(name) {
  const full = String(name ?? '').trim()
  const first = await lookupAgency(full)
  const org = full.split(/\s+/)[0]
  if (first.lookupStatus !== 'UNVERIFIED' || !org || org === full) return { ...first, byOrgName: false }
  const second = await lookupAgency(org)
  if (second.lookupStatus === 'UNVERIFIED') return { ...first, byOrgName: false }
  return { ...second, byOrgName: true, orgName: org }
}

/** 전화번호를 숫자만 남겨 비교한다. 둘 중 하나라도 없으면 비교하지 않는다(false). */
export function phonesDiffer(a, b) {
  const da = String(a ?? '').replace(/\D/g, '')
  const db = String(b ?? '').replace(/\D/g, '')
  return Boolean(da && db && da !== db)
}

export const PHONE_TYPE_LABEL = {
  REPRESENTATIVE: '대표·상담 번호',
  DEPARTMENT: '부서 번호',
  UNKNOWN: '번호 종류 확인 안 됨',
}

/** 기관 이름과 부서를 한 줄로 */
export function agencyTitle(a) {
  return a.department ? `${a.name} ${a.department}` : a.name
}

/**
 * 전화하거나 방문할 때 화면을 보여주며 쓸 한국어 문장을 문서 정보로 만든다.
 * 서버 문서는 기관별 안내 문장이 없으므로 제목·마감일·준비서류만으로 만든다.
 */
export function buildCallScript(doc) {
  const asks = []
  const deadline = docDeadline(doc)
  // 담당자에게 보여줄 문장이라 날짜도 한국어 형식으로 쓴다
  if (deadline) asks.push(`기한이 ${fmtDate(deadline, 'ko')}이 맞는지`)
  else asks.push('언제까지 처리해야 하는지')
  asks.push(doc.requiredDocs?.length ? `준비할 서류(${doc.requiredDocs.join(', ')})가 맞는지` : '어떤 서류를 준비해야 하는지')
  asks.push('어디에서 어떻게 처리하면 되는지')
  return `안녕하세요. 저는 외국인 주민이고 한국어가 서툽니다.\n받은 문서 「${doc.title}」에 대해 문의드립니다.\n${asks.join(', ')} 알려 주세요.\n천천히, 쉬운 말로 설명해 주시면 감사하겠습니다.`
}
