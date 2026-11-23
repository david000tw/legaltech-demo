import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { CitationView } from '../api'
import CitationTag from './CitationTag'
import ExportButton, { reasonLabel } from './ExportButton'
import SourcePane from './SourcePane'

const cite = (over: Partial<CitationView>): CitationView => ({
  id: 'c1', code: 'J1', kind: 'judgment', quote: 'q', label: '臺中地院113中簡3946', verify_status: 'ok',
  verify_reasons: [], lawyer_confirmed: false, court_confirmed: false, ...over,
})

describe('CitationTag', () => {
  it('is red when verification failed', () => {
    render(<CitationTag citation={cite({ verify_status: 'failed', verify_reasons: ['quote_not_found'] })} onClick={() => {}} />)
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'failed')
    expect(screen.getByRole('button').className).toMatch(/red/)
  })

  it('is yellow for a verified but unconfirmed judgment', () => {
    render(<CitationTag citation={cite({})} onClick={() => {}} />)
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'unconfirmed')
    expect(screen.getByRole('button').className).toMatch(/amber|yellow/)
  })

  it('is green when verified (and confirmed for judgments)', async () => {
    const onClick = vi.fn()
    render(<CitationTag citation={cite({ kind: 'evidence', code: 'E1', label: '原證1第1頁' })} onClick={onClick} />)
    const b = screen.getByRole('button', { name: /原證1第1頁/ })
    expect(b).toHaveAttribute('data-state', 'ok')
    expect(b.className).toMatch(/green|emerald/)
    await userEvent.click(b)
    expect(onClick).toHaveBeenCalled()
  })
})

describe('ExportButton', () => {
  it('is disabled and explains every blocker in Chinese', () => {
    render(<ExportButton blockers={[{ paragraph_seq: 1, reason: 'judgment_unconfirmed:J1' },
      { paragraph_seq: 2, reason: 'unbound_reference:999年度台上字第1號' }]} onExport={() => {}} />)
    expect(screen.getByRole('button', { name: '匯出 Word' })).toBeDisabled()
    expect(screen.getByText(/判決引用尚未確認（J1）/)).toBeInTheDocument()
    expect(screen.getByText(/999年度台上字第1號/)).toBeInTheDocument()
  })

  it('is enabled without blockers', async () => {
    const onExport = vi.fn()
    render(<ExportButton blockers={[]} onExport={onExport} />)
    await userEvent.click(screen.getByRole('button', { name: '匯出 Word' }))
    expect(onExport).toHaveBeenCalled()
  })

  it('has a Chinese label for every verification reason code', () => {
    for (const code of ['unreviewed', 'malformed_marker', 'unbound_reference:x', 'marker_without_citation:J1',
      'citation_without_marker:J1', 'quote_marker_without_quote:J1', 'inline_quote_unverified:x',
      'citation_failed:J1', 'judgment_unconfirmed:J1', 'unknown_code', 'quote_not_found', 'quote_too_short',
      'not_court_reasoning', 'source_withdrawn', 'statute_missing', 'possible_party_claim',
      'quote_contains_case_number', 'marker_in_heading', 'control_char', 'empty_draft', 'source_superseded']) {
      expect(reasonLabel(code)).not.toContain(code.split(':')[0])
    }
  })
})

describe('SourcePane', () => {
  it('highlights the quoted span', () => {
    const { container } = render(<SourcePane source={{ kind: 'judgment', title: '判決', text: '甲乙丙丁戊', span: [1, 3] }} />)
    expect(container.querySelector('mark')?.textContent).toBe('乙丙')
  })

  it('prompts when nothing is selected', () => {
    render(<SourcePane source={null} />)
    expect(screen.getByText(/點選引用/)).toBeInTheDocument()
  })
})
