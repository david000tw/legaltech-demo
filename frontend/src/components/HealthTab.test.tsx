import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type * as api from '../api'
import HealthTab from './HealthTab'

vi.mock('./HealthCheckPanel', () => ({ default: ({ roundId }: { roundId: string }) => <p>{`健檢面板 ${roundId}`}</p> }))

const round = (over: Partial<api.RoundSummary>): api.RoundSummary => ({ id: 'r1', seq: 1, status: 'drafted', error: null,
  brief_kind: 'answer', ...over })

describe('HealthTab', () => {
  it('lists a round whose drafting failed but whose health check ran (設計 6.3)', () => {
    render(<HealthTab rounds={[round({ id: 'r2', seq: 2, status: 'failed', failed_stage: 'draft', has_health: true })]} />)
    expect(screen.getByText('健檢面板 r2')).toBeInTheDocument()
  })

  it('still lists drafted rounds and skips rounds without any health result', () => {
    render(<HealthTab rounds={[round({}), round({ id: 'r3', seq: 3, status: 'extracted', has_health: false })]} />)
    expect(screen.getByText('健檢面板 r1')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /第3輪/ })).not.toBeInTheDocument()
  })
})
