// 段落內文的引用標記 {{cite:X}}／{{quote:X}} 與編輯框裡「一整塊的出處」之間的轉換（設計 5.3）。
// 存到後端的仍是原本的標記字串；編輯框只是把標記顯示成不能拆開的一塊。
import type { CitationView } from '../api'

export type Segment = { type: 'text'; text: string } | { type: 'marker'; kind: 'cite' | 'quote'; code: string }

const MARKER = /\{\{(cite|quote):([A-Z]\d+)\}\}/g  // 與 DraftEditor.save 保留引用的規則相同

export function splitMarkers(text: string): Segment[] {
  const out: Segment[] = []
  let last = 0
  for (const m of text.matchAll(MARKER)) {
    const at = m.index ?? 0
    if (at > last) out.push({ type: 'text', text: text.slice(last, at) })
    out.push({ type: 'marker', kind: m[1] as 'cite' | 'quote', code: m[2] })
    last = at + m[0].length
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) })
  return out
}

export function joinMarkers(segments: Segment[]): string {
  return segments.map((s) => (s.type === 'text' ? s.text : `{{${s.kind}:${s.code}}}`)).join('')
}

export function markerLabel(seg: Extract<Segment, { type: 'marker' }>, citations: CitationView[]): string {
  const c = citations.find((x) => x.code === seg.code)
  if (seg.kind === 'quote') return c?.quote ? `〔引文：${c.quote.slice(0, 12)}…〕` : `〔引文：${seg.code}〕`
  return `〔${c?.label ?? seg.code}〕`
}

// 貼上的文字不能在存檔後變成真的標記：大括號一律換成全形（全文掃描仍會把字號、證物號標紅）
export function sanitizePasted(text: string): string {
  return text.replace(/\{/g, '｛').replace(/\}/g, '｝')
}

// 編輯器文件（ProseMirror JSON）：每一行一個段落節點，出處是行內原子節點 citation
export type EditorNode = { type: string; text?: string; attrs?: Record<string, string>; content?: EditorNode[] }
export type EditorDoc = { type: 'doc'; content: EditorNode[] }

export function toEditorDoc(text: string): EditorDoc {
  return {
    type: 'doc',
    content: text.split('\n').map((line) => {
      const content = splitMarkers(line).map((s): EditorNode => (s.type === 'text'
        ? { type: 'text', text: s.text }
        : { type: 'citation', attrs: { kind: s.kind, code: s.code } }))
      return content.length ? { type: 'paragraph', content } : { type: 'paragraph' }
    }),
  }
}

export function fromEditorDoc(doc: EditorDoc): string {
  return doc.content.map((para) => joinMarkers((para.content ?? []).flatMap((n): Segment[] => {
    if (n.type === 'text') return [{ type: 'text', text: n.text ?? '' }]
    if (n.type === 'citation' && n.attrs) {
      return [{ type: 'marker', kind: n.attrs.kind as 'cite' | 'quote', code: n.attrs.code }]
    }
    return []
  }))).join('\n')
}
