import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import ToolsPage from './ToolsPage'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, courtFee: vi.fn(), deadline: vi.fn(), toolDefaults: vi.fn() }
})

const DISCLAIMER = '試算僅供參考，律師須自行核對'

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/tools${search}`]}>
      <Routes><Route path="/tools" element={<ToolsPage />} /></Routes>
    </MemoryRouter>,
  )
}

const region = (name: string) => screen.getByRole('region', { name })

beforeEach(() => vi.resetAllMocks())

describe('ToolsPage', () => {
  it('always shows the disclaimer and the step-by-step basis', async () => {
    vi.mocked(api.courtFee).mockResolvedValue({ fee: 13_200, notes: [DISCLAIMER],
      steps: [{ label: '第一審裁判費', value: '13,200元', basis: '民事訴訟法第77條之13、第77條之27' }] })
    renderAt()
    expect(screen.getByText(DISCLAIMER)).toBeInTheDocument()
    const fee = region('裁判費')
    await userEvent.type(within(fee).getByLabelText('訴訟標的金額'), '1000000')
    await userEvent.click(within(fee).getByRole('button', { name: '試算' }))
    expect(api.courtFee).toHaveBeenCalledWith({ property: true, amount: 1_000_000, instance: 1 })
    expect(await within(fee).findByRole('columnheader', { name: '依據' })).toBeInTheDocument()
    expect(within(fee).getByText('民事訴訟法第77條之13、第77條之27')).toBeInTheDocument()
    expect(within(fee).getByText('裁判費：新臺幣13,200元')).toBeInTheDocument()
    expect(screen.getAllByText(DISCLAIMER).length).toBeGreaterThanOrEqual(1)
  })

  it('sends the appeal amount for a partial appeal', async () => {
    vi.mocked(api.courtFee).mockResolvedValue({ fee: 19_800, steps: [], notes: [DISCLAIMER] })
    renderAt()
    const fee = region('裁判費')
    await userEvent.type(within(fee).getByLabelText('訴訟標的金額'), '5000000')
    await userEvent.selectOptions(within(fee).getByLabelText('審級'), '3')
    await userEvent.type(within(fee).getByLabelText('上訴金額（部分上訴時填）'), '1000000')
    await userEvent.click(within(fee).getByRole('button', { name: '試算' }))
    expect(api.courtFee).toHaveBeenCalledWith({ property: true, amount: 5_000_000, instance: 3, appeal_amount: 1_000_000 })
  })

  it('prefills from the case', async () => {
    vi.mocked(api.toolDefaults).mockResolvedValue({ amount: 405_000, amount_label: '訴訟標的金額',
      deadline_procedure: 'family_litigation', copies: 3, notes: [DISCLAIMER],
      copies_steps: [{ label: '合計', value: '3份', basis: '民事訴訟法第119條' }] })
    renderAt('?case=c1')
    expect(api.toolDefaults).toHaveBeenCalledWith('c1')
    const fee = await screen.findByRole('region', { name: '裁判費' })  // 帶入值讀到後才顯示表單
    expect(within(fee).getByDisplayValue('405000')).toBeInTheDocument()
    expect(within(region('上訴、抗告期限')).getByLabelText('程序')).toHaveValue('family_litigation')
    expect(within(region('繕本份數')).getByLabelText('份數')).toHaveValue(3)
    expect(within(region('繕本份數')).getByText('民事訴訟法第119條')).toBeInTheDocument()
  })

  it('sends both services and the local-attorney flag', async () => {
    vi.mocked(api.deadline)
      .mockRejectedValueOnce(new api.ApiError(422, '家事訴訟事件請選上訴；家事非訟事件請選抗告；或改用自訂天數'))
      .mockResolvedValue({ due: '2026-03-25', notes: [DISCLAIMER, '颱風等臨時停止上班不在行事曆內，請自行確認'],
        steps: [{ label: '屆滿日', value: '民國115年3月25日（星期三）', basis: '生效日＋期間＋在途期間' }] })
    renderAt()
    const dl = region('上訴、抗告期限')
    await userEvent.type(within(dl).getByLabelText('本人 送達日'), '2026-03-01')
    await userEvent.selectOptions(within(dl).getByLabelText('本人 送達方式'), 'deposit')
    await userEvent.type(within(dl).getByLabelText('訴訟代理人 送達日'), '2026-03-05')
    await userEvent.clear(within(dl).getByLabelText('在途期間（日）'))
    await userEvent.type(within(dl).getByLabelText('在途期間（日）'), '5')
    await userEvent.click(within(dl).getByLabelText('訴訟代理人住居法院所在地'))
    await userEvent.click(within(dl).getByRole('button', { name: '試算' }))
    expect(await within(dl).findByText('家事訴訟事件請選上訴；家事非訟事件請選抗告；或改用自訂天數')).toBeInTheDocument()
    await userEvent.click(within(dl).getByRole('button', { name: '試算' }))
    expect(api.deadline).toHaveBeenLastCalledWith({ kind: 'appeal', procedure: 'civil', travel_days: 5,
      attorney_local: true, services: [{ who: 'party', served_on: '2026-03-01', method: 'deposit' },
        { who: 'attorney', served_on: '2026-03-05', method: 'direct' }] })
    expect(await within(dl).findByText('屆滿日：民國115年3月25日（星期三）')).toBeInTheDocument()
    expect(within(dl).getByText('颱風等臨時停止上班不在行事曆內，請自行確認')).toBeInTheDocument()
  })

  it('shows the travel-period hint', () => {
    renderAt()
    expect(within(region('上訴、抗告期限')).getByText('依法院訴訟當事人在途期間標準')).toBeInTheDocument()
  })
})
