// 建立／修改案件表單的資料轉換：畫面上的草稿 ↔ 後端格式（元件檔只放元件）
import type * as api from './api'

export type HeaderDraft = { case_type: string; court: string; case_number: string; division: string; cause: string
  addressee: string; attorneys: string; reference_date: string }

export const emptyHeaderDraft = (): HeaderDraft => ({ case_type: 'traffic_tort', court: '', case_number: '',
  division: '', cause: '', addressee: '', attorneys: '', reference_date: '' })

export function headerDraftFrom(c: api.CaseDetail): HeaderDraft {
  const h = c.header
  return { case_type: c.case_type, court: h.court ?? '', case_number: h.case_number ?? '', division: h.division ?? '',
    cause: h.cause ?? '', addressee: h.addressee ?? '', attorneys: h.attorneys.join('，'),
    reference_date: h.reference_date ?? '' }
}

// 訴訟代理人可用半形／全形逗號、分號或頓號分隔；空白不算分隔（「王大明 律師」「David Wang」是一位）
export const splitAttorneys = (s: string) => s.split(/[,，、;；]+/).map((t) => t.trim()).filter(Boolean)

// 與後端上限一致（最多 10 位、每位 100 字）；先在畫面上擋，不讓律師看到看不懂的 422
export function attorneyProblem(s: string): string | null {
  const names = splitAttorneys(s)
  if (names.length > 10) return '訴訟代理人最多 10 位'
  if (names.some((n) => n.length > 100)) return '訴訟代理人每位最多 100 字'
  return null
}

// 送給後端的狀頭（不含受文；受文只在修改狀頭時送）：空白欄位送 null＝清空
export function headerInput(d: HeaderDraft) {
  const v = (s: string) => s.trim() || null
  return { case_type: d.case_type, court: v(d.court), case_number: v(d.case_number), division: v(d.division),
    cause: v(d.cause), attorneys: splitAttorneys(d.attorneys), reference_date: d.reference_date || null }
}

export type PartyDraft = { id?: string; name: string; role: string; alignment: api.Alignment; legal_rep: string
  position_note: string }

export function toDrafts(parties: api.Party[]): PartyDraft[] {
  return parties.map((p) => ({ id: p.id, name: p.name, role: p.role, alignment: p.alignment,
    legal_rep: p.legal_rep ?? '', position_note: p.position_note ?? '' }))
}

// 送給後端的格式：空白的選填欄位送 null；新列不帶 id
export function toInputs(drafts: PartyDraft[]): api.PartyInput[] {
  return drafts.map((d) => ({
    ...(d.id ? { id: d.id } : {}),
    name: d.name.trim(), role: d.role, alignment: d.alignment,
    legal_rep: d.legal_rep.trim() || null, position_note: d.position_note.trim() || null,
  }))
}

