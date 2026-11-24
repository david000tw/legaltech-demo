import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MeContext } from '../MeContext'
import AppShell from './AppShell'

vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return { ...real, logout: vi.fn() }
})

const me = (role: string): api.Me => ({ id: 'm1', firm_id: 'f1', email: 'x@a.test', display_name: '王律師', role,
  bar_number: null, bar_verified: role === 'lawyer' })

function renderShell(role: string, props: { caseTitle?: string; caseId?: string } = {}) {
  return render(
    <MeContext.Provider value={me(role)}>
      <MemoryRouter initialEntries={['/here']}>
        <Routes>
          <Route path="/here" element={<AppShell {...props}><p>頁面內容</p></AppShell>} />
          <Route path="/login" element={<p>登入頁</p>} />
        </Routes>
      </MemoryRouter>
    </MeContext.Provider>,
  )
}

beforeEach(() => vi.resetAllMocks())

describe('AppShell', () => {
  it('shows the case title and links, and logs out', async () => {
    vi.mocked(api.logout).mockResolvedValue({ ok: true })
    renderShell('lawyer', { caseTitle: '林大明車禍案' })
    expect(screen.getByRole('link', { name: 'AI 訴訟輔助系統' })).toHaveAttribute('href', '/cases')
    expect(screen.getByText('林大明車禍案')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '裁判費與期限試算' })).toHaveAttribute('href', '/tools')
    expect(screen.getByRole('link', { name: '用量與費用' })).toHaveAttribute('href', '/usage')
    expect(screen.getByText('頁面內容')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '登出' }))
    expect(api.logout).toHaveBeenCalled()
    expect(await screen.findByText('登入頁')).toBeInTheDocument()
  })

  it('hides the usage link for assistants', () => {
    renderShell('assistant')
    expect(screen.queryByRole('link', { name: '用量與費用' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '裁判費與期限試算' })).toBeInTheDocument()
  })

  it('passes the case id to the calculator link', () => {
    renderShell('firm_admin', { caseTitle: '林大明車禍案', caseId: 'c1' })
    expect(screen.getByRole('link', { name: '裁判費與期限試算' })).toHaveAttribute('href', '/tools?case=c1')
    expect(screen.getByRole('link', { name: '用量與費用' })).toBeInTheDocument()
  })
})
