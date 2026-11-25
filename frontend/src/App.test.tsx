import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter, useNavigate, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import App from './App'

vi.mock('./api', async (orig) => {
  const real = await orig<typeof import('./api')>()
  return { ...real, me: vi.fn() }
})

// 假的案件頁：每次掛載領一個流水號，用來判斷換案件時有沒有重新掛載（未儲存的修改會跟著狀態留下來）
vi.mock('./pages/CaseDetail', () => {
  let mounts = 0
  return {
    default: function FakeCaseDetail() {
      const { caseId } = useParams()
      const [n] = useState(() => ++mounts)
      return <p>{`案件 ${caseId}，第 ${n} 次掛載`}</p>
    },
  }
})

function GoTo({ path }: { path: string }) {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate(path)}>{`前往 ${path}`}</button>
}

describe('App routes', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.me).mockResolvedValue({ id: 'm1', firm_id: 'f1', email: 'a@a.test', display_name: 'a', role: 'lawyer',
      bar_number: null, bar_verified: true })
  })

  it('remounts the case page when the route switches to another case', async () => {
    render(<MemoryRouter initialEntries={['/cases/c1']}><GoTo path="/cases/c2" /><App /></MemoryRouter>)
    expect(await screen.findByText('案件 c1，第 1 次掛載')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '前往 /cases/c2' }))
    expect(await screen.findByText('案件 c2，第 2 次掛載')).toBeInTheDocument()
  })
})
