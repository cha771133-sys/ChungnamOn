// ─────────────────────────────────────────────
// 온보딩 다국어 문구 (데모: 베트남어·영어·한국어)
// 그 밖의 언어를 고르면 영어로 보여줍니다.
// ─────────────────────────────────────────────

const UI_LANGS = ['vi', 'en', 'ko']

export function uiLang(lang) {
  return UI_LANGS.includes(lang) ? lang : 'en'
}

export function t(lang, key) {
  const entry = T[key]
  if (!entry) return key
  return entry[uiLang(lang)] ?? entry.en
}

const T = {
  // 언어 선택
  langTitle: { ko: '사용할 언어를 선택하세요', vi: 'Chọn ngôn ngữ của bạn', en: 'Choose your language' },
  langSub: { ko: '나중에 언제든 바꿀 수 있어요.', vi: 'Bạn có thể thay đổi bất cứ lúc nào.', en: 'You can change this anytime.' },
  langFallback: {
    ko: '데모에는 베트남어, 영어, 한국어만 들어 있어요. 이 언어는 영어로 보여드려요.',
    vi: 'Bản demo chỉ có tiếng Việt, tiếng Anh và tiếng Hàn. Ngôn ngữ này sẽ hiển thị bằng tiếng Anh.',
    en: 'The demo includes Vietnamese, English, and Korean only. This language will be shown in English.',
  },
  langChanged: { ko: '안내 언어를 바꿨어요', vi: 'Đã đổi ngôn ngữ', en: 'Language changed' },
  langSheetTitle: { ko: '안내 언어', vi: 'Ngôn ngữ', en: 'Language' },
  continue: { ko: '다음', vi: 'Tiếp tục', en: 'Continue' },
  start: { ko: '시작하기', vi: 'Bắt đầu', en: 'Get started' },
  skip: { ko: '건너뛰기', vi: 'Bỏ qua', en: 'Skip' },
  back: { ko: '뒤로 가기', vi: 'Quay lại', en: 'Go back' },

  // 소개 슬라이드
  s1Title: { ko: '받은 문서를 사진으로 찍어 올려요', vi: 'Chụp ảnh giấy tờ bạn nhận được', en: 'Take a photo of the document you received' },
  s1Desc: {
    ko: '고지서, 안내문, 민원서류를 사진이나 PDF로 올리면 돼요.',
    vi: 'Tải lên giấy báo, thông báo hoặc hồ sơ hành chính bằng ảnh hoặc PDF.',
    en: 'Upload bills, notices, or civil documents as a photo or PDF.',
  },
  s2Title: {
    ko: '내 언어로 풀어 설명하고 공식 자료와 대조해요',
    vi: 'Giải thích bằng ngôn ngữ của bạn và đối chiếu với tài liệu chính thức',
    en: 'Explained in your language and checked against official sources',
  },
  s2Desc: {
    ko: '충남과 정부의 공식 자료로 확인한 내용인지 출처와 함께 보여드려요.',
    vi: 'Chúng tôi cho biết nội dung nào đã được xác minh bằng tài liệu chính thức của tỉnh Chungnam và chính phủ, kèm nguồn.',
    en: 'We show which details are confirmed by official Chungnam and government sources, with the source.',
  },
  s3Title: { ko: '할 일과 마감일을 챙겨드려요', vi: 'Nhắc việc cần làm và hạn chót', en: 'Never miss a task or deadline' },
  s3Desc: {
    ko: '체크리스트로 할 일을 정리하고, 마감 7일 전과 1일 전에 알려드려요.',
    vi: 'Sắp xếp việc cần làm bằng danh sách kiểm tra và nhắc bạn 7 ngày và 1 ngày trước hạn.',
    en: 'Organize tasks with a checklist and get reminders 7 days and 1 day before the deadline.',
  },
  chip: { ko: '6,250원을 내야 해요', vi: 'Bạn cần nộp 6.250 won', en: 'You need to pay 6,250 won' },
  upcoming: { ko: '다가오는 마감', vi: 'Hạn sắp tới', en: 'Upcoming deadline' },
  taskTax: { ko: '주민세 내기', vi: 'Nộp thuế cư trú', en: 'Pay resident tax' },
  chkCard: { ko: '외국인등록증 준비', vi: 'Chuẩn bị thẻ người nước ngoài', en: 'Prepare residence card' },
  chkPay: { ko: '가상계좌로 납부', vi: 'Nộp qua tài khoản ảo', en: 'Pay by virtual account' },
  push7: { ko: '마감 7일 전이에요', vi: 'Còn 7 ngày đến hạn', en: '7 days left' },

  // 로그인 / 회원가입
  loginTitle: { ko: '로그인', vi: 'Đăng nhập', en: 'Log in' },
  loginLead: {
    ko: '올린 문서와 일정을 안전하게 보관하려면 로그인이 필요해요.',
    vi: 'Hãy đăng nhập để lưu giữ an toàn giấy tờ và lịch của bạn.',
    en: 'Log in to keep your documents and schedules safe.',
  },
  email: { ko: '이메일', vi: 'Email', en: 'Email' },
  password: { ko: '비밀번호', vi: 'Mật khẩu', en: 'Password' },
  errEmail: { ko: '이메일 형식을 확인하세요.', vi: 'Vui lòng kiểm tra định dạng email.', en: 'Please check your email format.' },
  errPw4: { ko: '비밀번호를 4자 이상 입력하세요.', vi: 'Nhập mật khẩu từ 4 ký tự trở lên.', en: 'Enter at least 4 characters.' },
  newHere: { ko: '처음이신가요?', vi: 'Lần đầu sử dụng?', en: 'New here?' },
  signup: { ko: '회원가입', vi: 'Đăng ký', en: 'Sign up' },
  demoNote: {
    ko: '데모: 아무 이메일과 4자 이상 비밀번호로 로그인돼요.',
    vi: 'Bản demo: đăng nhập bằng email bất kỳ và mật khẩu từ 4 ký tự.',
    en: 'Demo: log in with any email and a password of 4+ characters.',
  },
  pw8: { ko: '비밀번호 (8자 이상)', vi: 'Mật khẩu (từ 8 ký tự)', en: 'Password (8+ characters)' },
  pwConfirm: { ko: '비밀번호 확인', vi: 'Xác nhận mật khẩu', en: 'Confirm password' },
  errPw8: { ko: '비밀번호를 8자 이상 입력하세요.', vi: 'Nhập mật khẩu từ 8 ký tự trở lên.', en: 'Enter at least 8 characters.' },
  errPwMatch: { ko: '비밀번호가 서로 달라요.', vi: 'Mật khẩu không khớp.', en: 'Passwords do not match.' },
  join: { ko: '가입하기', vi: 'Tạo tài khoản', en: 'Create account' },
  haveAccount: { ko: '이미 계정이 있나요?', vi: 'Đã có tài khoản?', en: 'Already have an account?' },

  // 프로필
  profileTitle: { ko: '내 정보를 알려주세요', vi: 'Cho chúng tôi biết về bạn', en: 'Tell us about yourself' },
  profileLead: {
    ko: '국적과 체류자격에 따라 적용되는 절차가 달라서, 맞춤 안내에 사용해요.',
    vi: 'Thủ tục khác nhau tùy theo quốc tịch và tư cách lưu trú, nên chúng tôi dùng thông tin này để hướng dẫn phù hợp.',
    en: 'Procedures differ by nationality and visa status, so we use this to tailor your guidance.',
  },
  fLang: { ko: '안내 언어', vi: 'Ngôn ngữ hướng dẫn', en: 'Language' },
  fNat: { ko: '국적', vi: 'Quốc tịch', en: 'Nationality' },
  fVisa: { ko: '체류자격', vi: 'Tư cách lưu trú', en: 'Visa status' },
  choose: { ko: '선택하세요', vi: 'Chọn', en: 'Select' },
  errNat: { ko: '국적을 선택하세요.', vi: 'Vui lòng chọn quốc tịch.', en: 'Please select your nationality.' },
  errVisa: { ko: '체류자격을 선택하세요.', vi: 'Vui lòng chọn tư cách lưu trú.', en: 'Please select your visa status.' },
  profileHint: {
    ko: '이 정보는 안내를 돕기 위한 것이며 공식 자격 판정에 쓰이지 않아요. 설정에서 언제든 바꿀 수 있어요.',
    vi: 'Thông tin này chỉ để hỗ trợ hướng dẫn, không dùng để xác định tư cách chính thức. Bạn có thể đổi trong phần Cài đặt.',
    en: 'This only helps us guide you and is not used for official eligibility decisions. You can change it in Settings.',
  },
  saveContinue: { ko: '저장하고 계속하기', vi: 'Lưu và tiếp tục', en: 'Save and continue' },

  // 알림 권한
  notifyTitle: {
    ko: '마감일을 놓치지 않게 알려드릴게요',
    vi: 'Chúng tôi sẽ nhắc để bạn không lỡ hạn chót',
    en: 'We’ll remind you so you never miss a deadline',
  },
  notifyLead: {
    ko: '등록한 일정의 마감 7일 전과 1일 전에 알림을 보내요.',
    vi: 'Chúng tôi gửi thông báo 7 ngày và 1 ngày trước hạn của lịch bạn đã lưu.',
    en: 'We send reminders 7 days and 1 day before each saved deadline.',
  },
  allow: { ko: '알림 켜기', vi: 'Bật thông báo', en: 'Turn on notifications' },
  later: { ko: '나중에', vi: 'Để sau', en: 'Later' },
  now: { ko: '지금', vi: 'Bây giờ', en: 'now' },
  pushTitle: {
    ko: '주민세 내기 마감 7일 전이에요',
    vi: 'Còn 7 ngày đến hạn nộp thuế cư trú',
    en: '7 days left to pay resident tax',
  },
  pushBody: {
    ko: '11월 30일까지 아산시청 세무과에 납부하세요.',
    vi: 'Hãy nộp cho Phòng Thuế, Tòa thị chính Asan trước ngày 30/11.',
    en: 'Pay the Tax Division of Asan City Hall by Nov 30.',
  },
}

export const NATION_NAMES = {
  베트남: { vi: 'Việt Nam', en: 'Vietnam' },
  중국: { vi: 'Trung Quốc', en: 'China' },
  우즈베키스탄: { vi: 'Uzbekistan', en: 'Uzbekistan' },
  네팔: { vi: 'Nepal', en: 'Nepal' },
  캄보디아: { vi: 'Campuchia', en: 'Cambodia' },
  필리핀: { vi: 'Philippines', en: 'Philippines' },
  태국: { vi: 'Thái Lan', en: 'Thailand' },
  인도네시아: { vi: 'Indonesia', en: 'Indonesia' },
  몽골: { vi: 'Mông Cổ', en: 'Mongolia' },
  기타: { vi: 'Khác', en: 'Other' },
}

export const VISA_NAMES = {
  'E-9': { vi: 'Lao động phổ thông', en: 'Non-professional employment' },
  'H-2': { vi: 'Lao động thăm thân', en: 'Working visit' },
  'F-4': { vi: 'Kiều bào nước ngoài', en: 'Overseas Korean' },
  'F-6': { vi: 'Kết hôn di trú', en: 'Marriage migrant' },
  'D-2': { vi: 'Du học', en: 'Student' },
  'D-4': { vi: 'Tu nghiệp phổ thông', en: 'General trainee' },
  'E-7': { vi: 'Hoạt động đặc định', en: 'Specific activities' },
  'F-2': { vi: 'Cư trú', en: 'Residence' },
  'F-5': { vi: 'Định cư vĩnh viễn', en: 'Permanent residence' },
  ETC: { vi: 'Khác', en: 'Other' },
}
