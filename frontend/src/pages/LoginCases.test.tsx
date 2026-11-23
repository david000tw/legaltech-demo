import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import Cases from './Cases'
import Login from './Login'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, login: vi.fn(), listCases: vi.fn(), createCase: vi.fn() }
})

beforeEach(() => vi.resetAllMocks())

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

describe('Cases', () => {
  it('lists cases and creates a new one', async () => {
    vi.mocked(api.listCases).mockResolvedValue([{ id: 'c1', title: '林大明車禍案', our_side: 'plaintiff', case_type: 'traffic_tort' }])
    vi.mocked(api.createCase).mockResolvedValue({ id: 'c2', title: '新案', our_side: 'defendant', case_type: 'traffic_tort' })
    render(<MemoryRouter><Cases /></MemoryRouter>)
    expect(await screen.findByRole('link', { name: /林大明車禍案/ })).toHaveAttribute('href', '/cases/c1')
    await userEvent.type(screen.getByLabelText('案件名稱'), '新案')
    await userEvent.selectOptions(screen.getByLabelText('我方立場'), 'defendant')
    await userEvent.click(screen.getByRole('button', { name: '建立案件' }))
    expect(api.createCase).toHaveBeenCalledWith('新案', 'defendant')
  })
})
