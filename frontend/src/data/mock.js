// ─────────────────────────────────────────────
// 데모용 목업 데이터
// 실제 개발 시 백엔드 API 응답(명세서 4장 데이터 흐름)으로 교체하세요.
// 전화번호·금액 등은 시연용 가상 값입니다.
// ─────────────────────────────────────────────

export const LANGS = [
  { code: 'vi', label: 'Tiếng Việt', ko: '베트남어', flag: '🇻🇳' },
  { code: 'en', label: 'English', ko: '영어', flag: '🇬🇧' },
  { code: 'zh', label: '中文', ko: '중국어', flag: '🇨🇳' },
  { code: 'uz', label: "O'zbekcha", ko: '우즈베크어', flag: '🇺🇿' },
  { code: 'ne', label: 'नेपाली', ko: '네팔어', flag: '🇳🇵' },
  { code: 'th', label: 'ไทย', ko: '태국어', flag: '🇹🇭' },
  { code: 'ko', label: '한국어', ko: '한국어', flag: '🇰🇷' },
]

// 서버에는 영문 대문자 두 자리 코드로 저장한다 (ZZ는 서비스 내부의 ‘기타’ 코드)
export const NATIONALITIES = [
  { code: 'VN', ko: '베트남' },
  { code: 'CN', ko: '중국' },
  { code: 'UZ', ko: '우즈베키스탄' },
  { code: 'NP', ko: '네팔' },
  { code: 'KH', ko: '캄보디아' },
  { code: 'PH', ko: '필리핀' },
  { code: 'TH', ko: '태국' },
  { code: 'ID', ko: '인도네시아' },
  { code: 'MN', ko: '몽골' },
  { code: 'ZZ', ko: '기타' },
]

export const VISAS = [
  { code: 'E-9', label: 'E-9 비전문취업' },
  { code: 'H-2', label: 'H-2 방문취업' },
  { code: 'F-4', label: 'F-4 재외동포' },
  { code: 'F-6', label: 'F-6 결혼이민' },
  { code: 'D-2', label: 'D-2 유학' },
  { code: 'D-4', label: 'D-4 일반연수' },
  { code: 'E-7', label: 'E-7 특정활동' },
  { code: 'F-2', label: 'F-2 거주' },
  { code: 'F-5', label: 'F-5 영주' },
  { code: 'ETC', label: '기타' },
]

export const SOURCES = {
  s1: {
    agency: '아산시청',
    title: '아산시 지방세 납부 안내',
    url: 'https://www.asan.go.kr',
    checkedAt: '2026-10-30',
    publishedAt: '2026-03-02',
  },
  s2: {
    agency: '아산시청',
    title: '아산시 부서별 연락처',
    url: 'https://www.asan.go.kr',
    checkedAt: '2026-10-30',
    publishedAt: null,
  },
  s3: {
    agency: '행정안전부',
    title: '위택스 지방세 납부 방법',
    url: 'https://www.wetax.go.kr',
    checkedAt: '2026-10-29',
    publishedAt: '2026-01-15',
  },
  s4: {
    agency: '국민건강보험공단',
    title: '외국인 지역가입자 보험료 안내',
    url: 'https://www.nhis.or.kr',
    checkedAt: '2026-10-28',
    publishedAt: null,
  },
}

const D1 = {
  id: 'd1',
  templateId: 'd1',
  docType: '고지서',
  title: '주민세 납부 안내',
  status: 'done',
  uploadedAt: '2026-11-01',
  maskedCount: 3,
  sender: { name: '아산시청 세무과' },
  deadline: '2026-11-30',
  task: {
    ko: '주민세 6,250원 내기',
    vi: 'Nộp thuế cư trú 6.250 won',
    en: 'Pay resident tax of 6,250 won',
  },
  requiredDocs: ['고지서', '외국인등록증'],
  summary: {
    ko: ['주민세(개인분) 6,250원을 내야 해요.', '마감일은 2026년 11월 30일이에요.', '가상계좌, 위택스, 은행 창구에서 낼 수 있어요.'],
    vi: [
      'Bạn cần nộp thuế cư trú (phần cá nhân) 6.250 won.',
      'Hạn nộp là ngày 30/11/2026.',
      'Có thể nộp qua tài khoản ảo, trang Wetax hoặc tại quầy ngân hàng.',
    ],
    en: [
      'You need to pay the resident tax (individual portion) of 6,250 won.',
      'The deadline is November 30, 2026.',
      'You can pay by virtual account, on Wetax, or at a bank counter.',
    ],
  },
  translated: {
    ko: `2026년 주민세(개인분)를 내라는 안내예요.
내는 사람: ●●● (개인정보를 가렸어요)
내야 할 돈: 6,250원
마감일: 2026년 11월 30일
마감일까지 내지 않으면 돈을 더 내야 해요(가산세).
내는 방법: 고지서에 적힌 가상계좌로 입금, 위택스 사이트, 은행 창구
문의: 아산시청 세무과 041-000-1234`,
    vi: `Giấy báo nộp thuế cư trú (phần cá nhân) năm 2026.
Người nộp thuế: ●●● (đã che thông tin cá nhân)
Số tiền phải nộp: 6.250 won
Hạn nộp: ngày 30 tháng 11 năm 2026
Nếu không nộp trước hạn, bạn sẽ phải trả thêm tiền phạt.
Cách nộp: chuyển khoản vào tài khoản ảo ghi trên giấy báo, nộp qua trang Wetax, hoặc nộp tại quầy ngân hàng.
Liên hệ: Phòng Thuế, Tòa thị chính Asan 041-000-1234`,
    en: `This is a notice to pay the 2026 resident tax (individual portion).
Taxpayer: ●●● (personal information hidden)
Amount due: 6,250 won
Deadline: November 30, 2026
If you do not pay by the deadline, an extra charge will be added.
How to pay: transfer to the virtual account on the notice, pay on the Wetax website, or pay at a bank counter.
Contact: Tax Division, Asan City Hall 041-000-1234`,
  },
  original: `2026년도 주민세(개인분) 납세고지서
납세자: 응●●● 귀하
주소: 충청남도 아산시 ●●●●●●●
외국인등록번호: ●●●●●●-●●●●●●●
납부할 세액: 6,250원
납부기한: 2026. 11. 30.
납부기한까지 납부하지 아니하면 가산세가 부과됩니다.
납부방법: 고지서 기재 가상계좌 입금, 위택스(www.wetax.go.kr), 금융기관 창구
문의: 아산시청 세무과 041-000-1234`,
  terms: [
    {
      word: '납세고지서',
      explain: {
        ko: '세금을 내라고 알려주는 공식 문서예요.',
        vi: 'Giấy chính thức thông báo bạn phải nộp thuế.',
        en: 'An official notice telling you to pay a tax.',
      },
    },
    {
      word: '주민세',
      explain: {
        ko: '그 지역에 사는 사람이 내는 세금이에요.',
        vi: 'Thuế mà người sống tại địa phương phải nộp.',
        en: 'A tax paid by people living in the area.',
      },
    },
    {
      word: '납부기한',
      explain: {
        ko: '돈을 내야 하는 마지막 날이에요.',
        vi: 'Ngày cuối cùng phải nộp tiền.',
        en: 'The last day to pay.',
      },
    },
    {
      word: '가산세',
      explain: {
        ko: '기한을 넘기면 추가로 붙는 세금이에요.',
        vi: 'Tiền thuế cộng thêm khi nộp trễ hạn.',
        en: 'An extra charge added when you pay late.',
      },
    },
    {
      word: '가상계좌',
      explain: {
        ko: '이 고지서만을 위해 만든 입금용 계좌예요.',
        vi: 'Tài khoản chỉ dùng để nộp tiền cho giấy báo này.',
        en: 'A bank account number made only for paying this bill.',
      },
    },
    {
      word: '위택스',
      explain: {
        ko: '지방세를 온라인으로 내는 정부 사이트예요.',
        vi: 'Trang web của chính phủ để nộp thuế địa phương trực tuyến.',
        en: 'A government website for paying local taxes online.',
      },
    },
  ],
  verification: [
    {
      id: 'v1',
      label: '납부 금액',
      docValue: '6,250원',
      status: 'MATCHED',
      officialValue: '개인분 주민세 세액 기준과 같아요',
      sources: ['s1'],
    },
    {
      id: 'v2',
      label: '납부 기한',
      docValue: '2026년 11월 30일',
      status: 'MATCHED',
      officialValue: '2026년 개인분 주민세 납부기한과 같아요',
      sources: ['s1'],
    },
    {
      id: 'v3',
      label: '외국인 납부 방법',
      docValue: '가상계좌, 위택스, 은행 창구',
      status: 'UNVERIFIED',
      reason: '외국인 주민이 위택스를 이용하는 조건을 공식 자료에서 찾지 못했어요.',
      sources: [],
    },
    {
      id: 'v4',
      label: '문의 전화번호',
      docValue: '041-000-1234',
      status: 'MISMATCH',
      officialValue: '041-000-5678',
      reason: '문서의 번호와 아산시 홈페이지의 번호가 달라요. 어느 쪽이 맞는지 단정할 수 없어요.',
      sources: ['s2'],
    },
  ],
  checklist: [
    {
      id: 'c1',
      text: '외국인등록증 준비',
      detail: '은행 창구에서 본인 확인을 요청할 수 있어요.',
      docs: ['외국인등록증'],
      state: 'CONFIRMED',
      done: true,
      sources: ['s1'],
    },
    {
      id: 'c2',
      text: '고지서의 가상계좌 번호 확인',
      detail: '고지서 아래쪽에 은행별 가상계좌가 적혀 있어요.',
      docs: ['고지서'],
      state: 'CONFIRMED',
      done: true,
      sources: ['s1'],
    },
    {
      id: 'c3',
      text: '가상계좌, 위택스, 은행 창구 중 한 곳에서 납부',
      method: '가상계좌 입금 또는 위택스 또는 은행 창구',
      due: '2026-11-30',
      state: 'NEEDS_CHECK',
      done: false,
      note: '외국인 주민의 위택스 이용 조건은 공식 자료로 확인되지 않았어요.',
      sources: ['s3'],
    },
    {
      id: 'c4',
      text: '외국인등록증 없이 납부할 수 있는지 확인',
      state: 'CONDITIONAL',
      done: false,
      note: '체류자격이나 상황에 따라 달라질 수 있어요.',
      sources: [],
    },
    {
      id: 'c5',
      text: '납부 영수증 보관',
      detail: '나중에 체류 관련 서류를 낼 때 필요할 수 있어요.',
      state: 'CONFIRMED',
      done: false,
      sources: ['s1'],
    },
  ],
  agency: {
    name: '아산시청 세무과',
    phone: '041-000-1234',
    sourceId: 's1',
    conflict: { phone: '041-000-5678', sourceId: 's2' },
  },
  callScript:
    '안녕하세요. 주민세 고지서를 받은 외국인 주민입니다. 외국인등록증 없이 가상계좌로 납부할 수 있는지 확인하고 싶습니다.',
  suggestedQuestions: ['외국인등록증이 없으면 어떻게 내나요?', '기한이 지나면 어떻게 되나요?', '분할 납부가 되나요?'],
}

const D2 = {
  id: 'd2',
  templateId: 'd2',
  docType: '안내문',
  title: '건강보험료 납부 안내',
  status: 'done',
  uploadedAt: '2026-10-20',
  maskedCount: 2,
  sender: { name: '국민건강보험공단 아산지사' },
  deadline: '2026-11-10',
  task: {
    ko: '11월분 건강보험료 내기',
    vi: 'Nộp phí bảo hiểm y tế tháng 11',
    en: 'Pay the November health insurance premium',
  },
  requiredDocs: ['고지서'],
  summary: {
    ko: ['11월분 건강보험료를 내야 해요.', '마감일은 2026년 11월 10일이에요.', '보험료를 내지 않으면 체류기간 연장 때 불이익이 있을 수 있어요.'],
    vi: [
      'Bạn cần nộp phí bảo hiểm y tế tháng 11.',
      'Hạn nộp là ngày 10/11/2026.',
      'Nếu không nộp, bạn có thể gặp bất lợi khi gia hạn thời gian lưu trú.',
    ],
    en: [
      'You need to pay the November health insurance premium.',
      'The deadline is November 10, 2026.',
      'Unpaid premiums can cause problems when you extend your stay.',
    ],
  },
  translated: {
    ko: `11월분 건강보험료 납부 안내예요.
가입자: ●●● (개인정보를 가렸어요)
마감일: 2026년 11월 10일
보험료를 내지 않으면 체류기간 연장 허가에 불이익이 있을 수 있어요.`,
    vi: `Thông báo nộp phí bảo hiểm y tế tháng 11.
Người tham gia: ●●● (đã che thông tin cá nhân)
Hạn nộp: ngày 10 tháng 11 năm 2026
Nếu không nộp phí, việc gia hạn thời gian lưu trú có thể bị ảnh hưởng.`,
    en: `Notice to pay the November health insurance premium.
Member: ●●● (personal information hidden)
Deadline: November 10, 2026
If you do not pay, extending your period of stay may be affected.`,
  },
  original: `2026년 11월분 지역가입자 건강보험료 납부 안내
가입자: 응●●● 귀하
납부기한: 2026. 11. 10.
보험료를 체납한 경우 체류기간 연장 허가가 제한될 수 있습니다.`,
  terms: [
    {
      word: '지역가입자',
      explain: {
        ko: '회사가 아닌 개인으로 건강보험에 가입한 사람이에요.',
        vi: 'Người tham gia bảo hiểm y tế với tư cách cá nhân, không qua công ty.',
        en: 'A person enrolled in health insurance individually, not through a company.',
      },
    },
    {
      word: '체납',
      explain: {
        ko: '내야 할 돈을 기한까지 내지 않은 상태예요.',
        vi: 'Tình trạng chưa nộp tiền đúng hạn.',
        en: 'Not paying money you owe by the deadline.',
      },
    },
  ],
  verification: [
    { id: 'v1', label: '납부 기한', docValue: '2026년 11월 10일', status: 'MATCHED', officialValue: '매월 10일 납부 기준과 같아요', sources: ['s4'] },
    { id: 'v2', label: '체납 시 불이익', docValue: '체류기간 연장 제한 가능', status: 'MATCHED', officialValue: '외국인 체납자 체류 제한 안내와 같아요', sources: ['s4'] },
  ],
  checklist: [
    { id: 'c1', text: '고지서의 납부 금액 확인', state: 'CONFIRMED', done: true, sources: ['s4'] },
    { id: 'c2', text: '가상계좌 또는 자동이체로 납부', method: '가상계좌 입금 또는 자동이체', due: '2026-11-10', state: 'CONFIRMED', done: false, sources: ['s4'] },
  ],
  agency: { name: '국민건강보험공단 아산지사', phone: '1577-1000', sourceId: 's4' },
  callScript: '안녕하세요. 건강보험료 고지서를 받은 외국인 주민입니다. 납부 방법을 확인하고 싶습니다.',
  suggestedQuestions: ['자동이체는 어떻게 신청하나요?', '이번 달 금액이 왜 올랐나요?'],
}

const D3 = { id: 'd3', docType: '안내문', title: '체류기간 연장 허가 안내', status: 'processing', uploadedAt: '2026-11-03' }

const D4 = {
  id: 'd4',
  docType: '통지서',
  title: '주정차 위반 사전 통지',
  status: 'failed',
  uploadedAt: '2026-10-27',
  failReason: '사진이 흐려 기한과 금액을 읽지 못했어요.',
}

export const INITIAL_DOCS = [D3, D1, D4, D2]

export const INITIAL_SCHEDULES = [
  { id: 'sc1', docId: 'd1', title: '주민세 내기', date: '2026-11-30', done: false },
  { id: 'sc2', docId: 'd2', title: '11월분 건강보험료 내기', date: '2026-11-10', done: false },
  { id: 'sc3', docId: 'd2', title: '10월분 건강보험료 내기', date: '2026-10-25', done: false },
]

// 새로 업로드한 문서는 데모에서 주민세 고지서 데이터를 복제해 보여줍니다.
export function makeDocFromTemplate(fileName) {
  const copy = JSON.parse(JSON.stringify(D1))
  const today = new Date().toISOString().slice(0, 10)
  return { ...copy, id: 'd' + Date.now(), uploadedAt: today, fileName }
}

// ─── 문서 챗봇 데모 답변 (실제로는 RAG 검색 + LLM 응답) ───
export const CHAT_KB = {
  d1: [
    {
      keys: ['등록증', 'thẻ', 'card', 'registration'],
      grounded: true,
      sources: ['s1'],
      answer: {
        ko: '고지서에 적힌 가상계좌로 입금하면 외국인등록증 없이도 낼 수 있어요. 은행 창구에서 낼 때는 고지서를 꼭 가져가세요.',
        vi: 'Nếu chuyển khoản vào tài khoản ảo ghi trên giấy báo, bạn có thể nộp mà không cần thẻ người nước ngoài. Khi nộp tại quầy ngân hàng, hãy mang theo giấy báo.',
        en: 'If you transfer to the virtual account on the notice, you can pay without your residence card. If you pay at a bank counter, bring the notice with you.',
      },
      follow: ['가상계좌는 어디에 적혀 있나요?', '기한이 지나면 어떻게 되나요?'],
    },
    {
      keys: ['기한', '늦', '지나', 'hạn', 'trễ', 'late', 'deadline'],
      grounded: true,
      sources: ['s1', 's3'],
      answer: {
        ko: '기한이 지나면 가산세가 붙어서 내야 할 돈이 늘어나요. 늘어나는 정확한 금액은 아산시청 세무과에 확인하세요.',
        vi: 'Nếu quá hạn, tiền phạt sẽ được cộng thêm nên số tiền phải nộp sẽ tăng. Hãy hỏi Phòng Thuế Tòa thị chính Asan để biết số tiền chính xác.',
        en: 'If you miss the deadline, an extra charge is added and the amount increases. Check the exact amount with the Tax Division of Asan City Hall.',
      },
      follow: ['외국인등록증이 없으면 어떻게 내나요?'],
    },
    {
      keys: ['가상계좌', 'tài khoản', 'account'],
      grounded: true,
      sources: ['s1'],
      answer: {
        ko: '고지서 아래쪽 납부 영역에 은행별 가상계좌 번호가 적혀 있어요. 원하는 은행의 번호로 입금하면 돼요.',
        vi: 'Số tài khoản ảo theo từng ngân hàng được ghi ở phần nộp tiền phía dưới giấy báo. Bạn chỉ cần chuyển khoản vào số của ngân hàng bạn muốn.',
        en: 'Virtual account numbers for each bank are printed in the payment section at the bottom of the notice. Transfer to the number for the bank you prefer.',
      },
      follow: ['기한이 지나면 어떻게 되나요?'],
    },
  ],
}

export const CHAT_FALLBACK = {
  ko: '이 질문은 공식 자료에서 근거를 찾지 못했어요. 정확한 내용은 담당기관에 확인해 주세요.',
  vi: 'Chúng tôi không tìm thấy căn cứ chính thức cho câu hỏi này. Vui lòng hỏi trực tiếp cơ quan phụ trách.',
  en: 'We could not find an official source for this question. Please check with the responsible office.',
}
