import { api } from './api'

/** FOUND: 기관 하나를 찾음 / AMBIGUOUS: 후보가 여러 개 / UNVERIFIED: 찾지 못함 */
export type LookupStatus = 'FOUND' | 'AMBIGUOUS' | 'UNVERIFIED'

/** REPRESENTATIVE: 대표·상담 번호 / DEPARTMENT: 부서 번호 / UNKNOWN: 미확인 */
export type PhoneType = 'REPRESENTATIVE' | 'DEPARTMENT' | 'UNKNOWN'

/** 공식 자료로 확인한 담당기관 */
export type AgencyInfo = {
  id: number
  name: string
  department: string
  region: string
  phone: string | null
  phoneType: PhoneType
  sourceUrl: string | null
  checkedAt: string | null
}

/** GET /api/sources/agencies/lookup/ 응답. 개인별 적용 여부는 항상 확인 필요(NEEDS_CONFIRMATION)다. */
export type AgencyLookup = {
  lookupStatus: LookupStatus
  applicabilityStatus: string
  agency: AgencyInfo | null
  candidates: AgencyInfo[]
  message: string
}

/** 기관 이름(공식명 또는 등록된 별칭과 정확히 일치)으로 담당기관 연락처를 찾는다. */
export async function lookupAgency(name: string, region?: string): Promise<AgencyLookup> {
  const params: Record<string, string> = { name }
  if (region) params.region = region
  const { data } = await api.get<AgencyLookup>('/api/sources/agencies/lookup/', { params })
  return data
}
