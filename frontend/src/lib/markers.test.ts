import { describe, expect, it } from 'vitest'
import type { CitationView } from '../api'
import { fromEditorDoc, joinMarkers, markerLabel, sanitizePasted, splitMarkers, toEditorDoc } from './markers'

const cite = (code: string, label: string | null, quote = ''): CitationView => ({ id: code, code, kind: 'evidence',
  quote, label, verify_status: 'ok', verify_reasons: [], lawyer_confirmed: false, court_confirmed: false })
const CITES = [cite('E1', '被證1第1頁'), cite('J1', '最高法院112台上123', '未在適當位置暫停禮讓幹線道之系爭車輛')]
const TEXT = '被告未讓幹道車先行（{{cite:E1}}），{{quote:J1}}（{{cite:J1}}參照）。'

describe('markers', () => {
  it('round-trips text with markers unchanged', () => {
    expect(joinMarkers(splitMarkers(TEXT))).toBe(TEXT)
    expect(splitMarkers(TEXT).filter((s) => s.type === 'marker')).toEqual([
      { type: 'marker', kind: 'cite', code: 'E1' }, { type: 'marker', kind: 'quote', code: 'J1' },
      { type: 'marker', kind: 'cite', code: 'J1' }])
  })

  it('labels cite and quote markers', () => {
    expect(markerLabel({ type: 'marker', kind: 'cite', code: 'E1' }, CITES)).toBe('〔被證1第1頁〕')
    expect(markerLabel({ type: 'marker', kind: 'quote', code: 'J1' }, CITES)).toBe('〔引文：未在適當位置暫停禮讓幹線…〕')
    expect(markerLabel({ type: 'marker', kind: 'cite', code: 'E9' }, CITES)).toBe('〔E9〕')
  })

  it('sanitizes braces in pasted text', () => {
    expect(sanitizePasted('{{cite:E9}}')).toBe('｛｛cite:E9｝｝')
  })

  it('converts text to an editor document and back, line by line', () => {
    const t = `${TEXT}\n\n第二行{{cite:E1}}`
    const doc = toEditorDoc(t)
    expect(doc.content).toHaveLength(3)
    expect(doc.content[0].content?.filter((n) => n.type === 'citation')).toHaveLength(3)
    expect(fromEditorDoc(doc)).toBe(t)
  })

  it('removing a chip from the document removes only that marker', () => {
    const doc = toEditorDoc(TEXT)
    const para = doc.content[0]
    const content = para.content!.filter((n) => !(n.type === 'citation' && n.attrs?.code === 'E1'))
    expect(fromEditorDoc({ ...doc, content: [{ ...para, content }] })).toBe('被告未讓幹道車先行（），{{quote:J1}}（{{cite:J1}}參照）。')
  })
})
