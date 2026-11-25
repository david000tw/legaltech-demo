import { render, waitFor } from '@testing-library/react'
import type { Editor } from '@tiptap/core'
import { describe, expect, it, vi } from 'vitest'
import type { CitationView } from '../api'
import CitationEditor from './CitationEditor'

const cite = (code: string, label: string): CitationView => ({ id: code, code, kind: 'evidence', quote: '',
  label, verify_status: 'ok', verify_reasons: [], lawyer_confirmed: false, court_confirmed: false })
const CITES = [cite('E1', '被證1第1頁'), cite('J1', '最高法院112台上123')]

describe('CitationEditor', () => {
  it('renders markers as non-editable chips with readable labels', async () => {
    const { container } = render(<CitationEditor value="被告未讓路（{{cite:E1}}），依{{cite:J1}}。" citations={CITES}
      onChange={vi.fn()} ariaLabel="段落內文" />)
    await waitFor(() => expect(container.querySelectorAll('[data-citation]')).toHaveLength(2))
    const chips = [...container.querySelectorAll('[data-citation]')]
    expect(chips.map((c) => c.textContent)).toEqual(['〔被證1第1頁〕', '〔最高法院112台上123〕'])
    expect(chips.every((c) => c.getAttribute('contenteditable') === 'false')).toBe(true)
    expect(container.textContent).not.toContain('{{cite:')
  })

  it('deleting one chip emits the text without that marker only', async () => {
    let editor: Editor | null = null
    const onChange = vi.fn()
    render(<CitationEditor value="甲{{cite:E1}}乙{{cite:J1}}丙" citations={CITES} onChange={onChange}
      ariaLabel="段落內文" onEditor={(e) => { editor = e }} />)
    await waitFor(() => expect(editor).not.toBeNull())
    // 文件位置：段落開頭 1，「甲」佔 1，E1 的塊在位置 2
    editor!.commands.deleteRange({ from: 2, to: 3 })
    expect(onChange).toHaveBeenLastCalledWith('甲乙{{cite:J1}}丙')
  })
})
