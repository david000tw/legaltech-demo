import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { PartyDraft } from '../caseForms'
import PartiesEditor from './PartiesEditor'

const WARNING = '尚未指定我方當事人，書狀的具狀人與訴訟代理人會是空白'
const party = (over: Partial<PartyDraft>): PartyDraft => ({ name: '宋文彬', role: '原告', alignment: 'ours',
  legal_rep: '', position_note: '', ...over })

function Harness({ initial }: { initial: PartyDraft[] }) {
  const [value, setValue] = useState(initial)
  return <PartiesEditor value={value} onChange={setValue} options={null} />
}

describe('PartiesEditor', () => {
  it('warns when parties exist but none is marked as ours', () => {
    render(<Harness initial={[party({ alignment: 'opposing', name: '宋文德' })]} />)
    expect(screen.getByText(WARNING)).toBeInTheDocument()
  })

  it('has no warning with no parties yet, or once one party is ours', async () => {
    const { unmount } = render(<Harness initial={[]} />)
    expect(screen.queryByText(WARNING)).toBeNull()
    unmount()
    render(<Harness initial={[party({ alignment: 'aligned', name: '宋文琪' })]} />)
    expect(screen.getByText(WARNING)).toBeInTheDocument()  // 立場相同不算我方
    await userEvent.selectOptions(screen.getByLabelText('當事人1 歸屬'), 'ours')
    expect(screen.queryByText(WARNING)).toBeNull()
  })
})
