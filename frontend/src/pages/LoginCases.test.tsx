import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import Cases from './Cases'
import Login from './Login'
import { OPTIONS } from '../test/caseOptions'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, login: vi.fn(), listCases: vi.fn(), createCase: vi.fn(), getCaseOptions: vi.fn(), putParties: vi.fn() }
})

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getCaseOptions).mockResolvedValue(OPTIONS)
})

describe('Login', () => {
  it('logs in and goes to the case list', async () => {
    vi.mocked(api.login).mockResolvedValue({ id: 'm1' })
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/cases" element={<p>案件列表頁</p>} />
        </Routes>
      </MemoryRouter>,
    )
    await userEvent.type(screen.getByLabelText('電子郵件'), 'lawyer@a.test')
    await userEvent.type(screen.getByLabelText('密碼'), 'pw')
    await userEvent.click(screen.getByRole('button', { name: '登入' }))
    expect(api.login).toHaveBeenCalledWith('lawyer@a.test', 'pw')
    expect(await screen.findByText('案件列表頁')).toBeInTheDocument()
  })

  it('shows the error when login fails', async () => {
    vi.mocked(api.login).mockRejectedValue(new api.ApiError(401, '帳號或密碼錯誤'))
    render(<MemoryRouter><Login /></MemoryRouter>)
    await userEvent.type(screen.getByLabelText('電子郵件'), 'x@a.test')
    await userEvent.type(screen.getByLabelText('密碼'), 'bad')
    await userEvent.click(screen.getByRole('button', { name: '登入' }))
    expect(await screen.findByText('帳號或密碼錯誤')).toBeInTheDocument()
  })
})

function renderCases() {
  return render(
    <MemoryRouter initialEntries={['/cases']}>
      <Routes>
        <Route path="/cases" element={<Cases />} />
        <Route path="/cases/:caseId" element={<p>案件頁</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Cases', () => {
  it('lists cases and creates a new one', async () => {
    vi.mocked(api.listCases).mockResolvedValue([{ id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort' }])
    vi.mocked(api.createCase).mockResolvedValue({ id: 'c2', title: '新案', our_side: 'defendant', case_type: 'traffic_tort' })
    renderCases()
    expect(await screen.findByRole('link', { name: /林大明車禍案/ })).toHaveAttribute('href', '/cases/c1')
    await userEvent.type(screen.getByLabelText('案件名稱'), '新案')
    await userEvent.selectOptions(screen.getByLabelText('我方立場'), 'defendant')
    await userEvent.click(screen.getByRole('button', { name: '建立案件' }))
    expect(api.createCase).toHaveBeenCalledWith(expect.objectContaining(
      { title: '新案', our_side: 'defendant', case_type: 'traffic_tort' }))
    expect(vi.mocked(api.createCase).mock.calls[0][0].attorneys).toBeUndefined()  // 沒填：後端預設帶入建立者
    expect(api.putParties).not.toHaveBeenCalled()
    expect(await screen.findByText('案件頁')).toBeInTheDocument()
  })

  it('creates a case with type and parties', async () => {
    vi.mocked(api.listCases).mockResolvedValue([])
    vi.mocked(api.createCase).mockResolvedValue({ id: 'c9', title: '宋文彬 分割遺產', our_side: 'plaintiff',
      case_type: 'estate_partition' })
    vi.mocked(api.putParties).mockResolvedValue([])
    renderCases()
    await userEvent.type(screen.getByLabelText('案件名稱'), '宋文彬 分割遺產')
    await screen.findByRole('option', { name: '分割遺產（含特留分）' })  // 選項從後端載入
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '案型' }), 'estate_partition')
    expect(screen.queryByLabelText('事故發生日')).not.toBeInTheDocument()  // 基準日名稱跟著案型換
    fireEvent.change(screen.getByLabelText('繼承開始日'), { target: { value: '2023-05-01' } })
    await userEvent.type(screen.getByLabelText('法院'), '臺灣臺北地方法院')
    await userEvent.type(screen.getByLabelText('案號'), '113年度家繼訴字第89號')
    await userEvent.type(screen.getByLabelText('股別'), '忠')
    await userEvent.type(screen.getByLabelText('案由'), '分割遺產')
    await userEvent.type(screen.getByLabelText('訴訟代理人（以逗號分隔）'), '王大明律師，李小華律師')

    await userEvent.click(screen.getByRole('button', { name: '新增當事人' }))
    await userEvent.type(screen.getByLabelText('當事人1 姓名'), '宋文彬')
    await userEvent.click(screen.getByRole('button', { name: '新增當事人' }))
    await userEvent.type(screen.getByLabelText('當事人2 姓名'), '宋文琪')
    await userEvent.selectOptions(screen.getByLabelText('當事人2 角色'), '被告')
    await userEvent.selectOptions(screen.getByLabelText('當事人2 歸屬'), 'aligned')
    await userEvent.type(screen.getByLabelText('當事人2 立場說明'), '同意分割，但爭執遺產範圍')
    await userEvent.click(screen.getByRole('button', { name: '新增當事人' }))
    await userEvent.type(screen.getByLabelText('當事人3 姓名'), '宋文德')
    await userEvent.selectOptions(screen.getByLabelText('當事人3 角色'), '被告')
    await userEvent.type(screen.getByLabelText('當事人3 法定代理人'), '宋大德')
    await userEvent.click(screen.getByRole('button', { name: '上移 當事人3' }))  // 對造排到立場相同者前面
    await userEvent.click(screen.getByRole('button', { name: '新增當事人' }))
    await userEvent.click(screen.getByRole('button', { name: '刪除 當事人4' }))

    await userEvent.click(screen.getByRole('button', { name: '建立案件' }))
    expect(api.createCase).toHaveBeenCalledWith({
      title: '宋文彬 分割遺產', our_side: 'plaintiff', case_type: 'estate_partition', court: '臺灣臺北地方法院',
      case_number: '113年度家繼訴字第89號', division: '忠', cause: '分割遺產', attorneys: ['王大明律師', '李小華律師'],
      reference_date: '2023-05-01',
    })
    expect(api.putParties).toHaveBeenCalledWith('c9', [
      { name: '宋文彬', role: '原告', alignment: 'ours', legal_rep: null, position_note: null },
      { name: '宋文德', role: '被告', alignment: 'opposing', legal_rep: '宋大德', position_note: null },
      { name: '宋文琪', role: '被告', alignment: 'aligned', legal_rep: null, position_note: '同意分割，但爭執遺產範圍' },
    ])
    expect(await screen.findByText('案件頁')).toBeInTheDocument()
  })
})
