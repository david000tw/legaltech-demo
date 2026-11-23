import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExportBlockedError, exportDraft, patchParagraph, reviewParagraph, uploadDocument } from './api'

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

  it('sends the heading with a paragraph edit', async () => {
    const f = respond(200, {})
    await patchParagraph('p1', '內文', [], '㈠標題')
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ text: '內文', citations: [], heading: '㈠標題' })
  })

  it('sends the filer with an opponent brief upload', async () => {
    const f = respond(200, { id: 'd1', page_count: 1 })
    await uploadDocument('c1', new File(['x'], 'a.txt'), 'opponent_brief', 'r1', 'pt2')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/cases/c1/documents')
    const form = init.body as FormData
    expect([form.get('kind'), form.get('round_id'), form.get('filed_by_party_id')]).toEqual(['opponent_brief', 'r1', 'pt2'])
  })
})
