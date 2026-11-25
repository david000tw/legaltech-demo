import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import HeaderAutoNotice from './HeaderAutoNotice'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, verifyHeader: vi.fn() }
})

const auto: Record<string, api.HeaderAutoField> = {
  case_number: { document_id: 'd1', filename: '準備書狀.pdf', page_no: 1, verified: false, value: '114年度訴字第123號' },
  court: { document_id: 'd1', filename: '準備書狀.pdf', page_no: 1, verified: true, value: '臺灣臺北地方法院' },
  division: { document_id: 'd1', filename: '準備書狀.pdf', page_no: 1, verified: true, value: null },
}
const parties = [{ id: 'p1', seq: 1, name: '林大明', role: '原告', alignment: 'opposing', legal_rep: null,
  position_note: null, auto: true, auto_verified: false }] as api.Party[]

describe('HeaderAutoNotice', () => {
  beforeEach(() => vi.resetAllMocks())

  it('lists auto-filled fields with their source and lets a lawyer confirm them', async () => {
    vi.mocked(api.verifyHeader).mockResolvedValue({ header_unverified: false })
    const onVerified = vi.fn(async () => {})
    const onOpenPage = vi.fn()
    render(<HeaderAutoNotice caseId="c1" auto={auto} parties={parties} unverified canVerify onVerified={onVerified}
      onError={vi.fn()} onOpenDocument={onOpenPage} />)
    expect(screen.getByText('案號「114年度訴字第123號」：從對方書狀「準備書狀.pdf」第 1 頁帶入，請核對')).toBeInTheDocument()
    expect(screen.getByText('法院「臺灣臺北地方法院」：從對方書狀「準備書狀.pdf」第 1 頁帶入（已核對）')).toBeInTheDocument()
    expect(screen.getByText('股別（已清空）：從對方書狀「準備書狀.pdf」第 1 頁帶入（已核對）')).toBeInTheDocument()
    expect(screen.getByText('林大明（原告）：從對方書狀自動加入，請核對')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '狀頭核對無誤' }))
    expect(api.verifyHeader).toHaveBeenCalledWith('c1', {
      fields: { case_number: '114年度訴字第123號', court: '臺灣臺北地方法院', division: null },
      parties: { p1: '林大明' },
    })
    expect(onVerified).toHaveBeenCalled()
    expect(screen.queryByText(/請看過後再按一次/)).not.toBeInTheDocument()
  })

  it('asks to look again when something new came in after the page opened (次要第 5 項)', async () => {
    vi.mocked(api.verifyHeader).mockResolvedValue({ header_unverified: true })
    render(<HeaderAutoNotice caseId="c1" auto={auto} parties={parties} unverified canVerify onVerified={vi.fn(async () => {})}
      onError={vi.fn()} onOpenDocument={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '狀頭核對無誤' }))
    expect(await screen.findByText('有剛自動帶入或剛被修改的項目，請看過後再按一次「狀頭核對無誤」')).toBeInTheDocument()
  })

  it('assistants see the notice but not the button', () => {
    render(<HeaderAutoNotice caseId="c1" auto={auto} parties={parties} unverified canVerify={false} onVerified={vi.fn()}
      onError={vi.fn()} onOpenDocument={vi.fn()} />)
    expect(screen.queryByRole('button', { name: '狀頭核對無誤' })).not.toBeInTheDocument()
  })

  it('shows nothing when nothing was filled in automatically', () => {
    const { container } = render(<HeaderAutoNotice caseId="c1" auto={{}} parties={[]} unverified={false} canVerify
      onVerified={vi.fn()} onError={vi.fn()} onOpenDocument={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('HeaderAutoNotice missing fields', () => {
  it('tells the lawyer which fields the opponent brief did not have', () => {
    render(<HeaderAutoNotice caseId="c1" auto={{}} parties={[]} missing={['case_number', 'division']} unverified={false}
      canVerify onVerified={vi.fn()} onError={vi.fn()} onOpenDocument={vi.fn()} />)
    expect(screen.getByText('案號：對方書狀沒寫，請自行填寫')).toBeInTheDocument()
    expect(screen.getByText('股別：對方書狀沒寫，請自行填寫')).toBeInTheDocument()
  })

  it('does not call a cleared auto-filled field missing (修正後複審)', () => {
    const cleared = { division: { document_id: 'd1', filename: '準備書狀.pdf', page_no: 1, verified: true, value: null } }
    render(<HeaderAutoNotice caseId="c1" auto={cleared} parties={[]} missing={['division', 'cause']} unverified={false}
      canVerify onVerified={vi.fn()} onError={vi.fn()} onOpenDocument={vi.fn()} />)
    expect(screen.getByText('股別（已清空）：從對方書狀「準備書狀.pdf」第 1 頁帶入（已核對）')).toBeInTheDocument()
    expect(screen.queryByText('股別：對方書狀沒寫，請自行填寫')).not.toBeInTheDocument()
    expect(screen.getByText('案由：對方書狀沒寫，請自行填寫')).toBeInTheDocument()
  })
})
