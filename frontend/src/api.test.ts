import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExportBlockedError, exportDraft, reviewParagraph } from './api'

function respond(status: number, body: unknown) {
  const f = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', f)
  return f
}

describe('api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('exports with POST and turns 409 into ExportBlockedError with the blockers', async () => {
    const f = respond(409, { blockers: [{ paragraph_seq: 2, reason: 'unreviewed' }] })
    const err = await exportDraft('dr1').catch((e) => e)
    expect(err).toBeInstanceOf(ExportBlockedError)
    expect(err.blockers).toEqual([{ paragraph_seq: 2, reason: 'unreviewed' }])
    expect(f).toHaveBeenCalledWith('/api/drafts/dr1/export', expect.objectContaining({ method: 'POST' }))
  })

  it('reports the server message for other export errors', async () => {
    respond(403, { detail: '限已驗證律師' })
    await expect(exportDraft('dr1')).rejects.toThrow('限已驗證律師')
  })

  it('sends the version the lawyer saw when marking a paragraph reviewed', async () => {
    const f = respond(200, {})
    await reviewParagraph('p1', 'v1')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/paragraphs/p1/review')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ version: 'v1' })
  })
})
