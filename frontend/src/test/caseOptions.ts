// 測試用：與後端 GET /api/case-options 回傳相同形狀的選項
import type { CaseOptions } from '../api'

export const OPTIONS: CaseOptions = {
  case_types: [
    { code: 'traffic_tort', label: '車禍侵權', procedure: 'civil', reference_date_label: '事故發生日' },
    { code: 'spousal_rights', label: '侵害配偶權', procedure: 'civil', reference_date_label: '侵害行為終了日' },
    { code: 'estate_partition', label: '分割遺產（含特留分）', procedure: 'family', reference_date_label: '繼承開始日' },
    { code: 'nominee_registration', label: '借名登記返還', procedure: 'civil', reference_date_label: '借名關係終止日' },
  ],
  party_roles: ['原告', '被告', '上訴人', '被上訴人', '反訴原告', '反訴被告', '聲請人', '相對人'],
  alignments: [
    { code: 'ours', label: '我方' }, { code: 'opposing', label: '對造' }, { code: 'aligned', label: '立場與我方相同' },
  ],
}
