import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError, downloadFilingPackage, ExportBlockedError, exportDraft, patchParagraph, reviewParagraph, startDocumentOcr,
  uploadDocument, uploadDocuments,
} from './api'

function respond(status: number, body: unknown) {
  const f = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', f)
  return f
}

describe('api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uploadDocuments uses files for photos and file for a single document', async () => {
    const f = respond(200, { id: 'd1', page_count: 1, ocr: null })
    const form = (n: number) => (f.mock.calls[n] as unknown as [string, RequestInit])[1].body as FormData
    const pdf = new File(['%PDF'], '診斷.pdf', { type: 'application/pdf' })
    const a = new File(['a'], 'a.JPG', { type: 'image/jpeg' })
    const b = new File(['b'], 'b.png', { type: 'image/png' })
    await uploadDocuments('c1', [pdf], 'evidence')
    expect(form(0).getAll('file')).toHaveLength(1)
    expect(form(0).getAll('files')).toHaveLength(0)
    await uploadDocuments('c1', [a, b], 'opponent_brief', 'r1', 'pt1')
    expect(form(1).getAll('files')).toHaveLength(2)
    expect([form(1).get('kind'), form(1).get('round_id'), form(1).get('filed_by_party_id')])
      .toEqual(['opponent_brief', 'r1', 'pt1'])
    await uploadDocuments('c1', [a], 'evidence')
    expect(form(2).getAll('files')).toHaveLength(1)
    expect(f).toHaveBeenCalledWith('/api/cases/c1/documents', expect.objectContaining({ method: 'POST' }))
  })

  it('uploadDocuments sends several non-photo files one by one, each as its own document', async () => {
    // 走查：一次選好幾份 PDF 被擋下很煩；改成逐份上傳，每份各自一個證物號
    const f = respond(200, { id: 'd1', page_count: 1, ocr: null })
    const pdf1 = new File(['%PDF'], '一.pdf', { type: 'application/pdf' })
    const pdf2 = new File(['%PDF'], '二.pdf', { type: 'application/pdf' })
    const jpg = new File(['a'], 'a.jpg', { type: 'image/jpeg' })
    await uploadDocuments('c1', [pdf1, pdf2, jpg], 'evidence')
    expect(f).toHaveBeenCalledTimes(3)
    const sent = f.mock.calls.map((c) => {
      const body = (c as unknown as [string, RequestInit])[1].body as FormData
      return [...body.getAll('file'), ...body.getAll('files')].map((x) => (x as File).name)
    })
    expect(sent).toEqual([['一.pdf'], ['二.pdf'], ['a.jpg']])
  })

  it('uploadDocuments stops at the first failed file and says which one', async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'd1', page_count: 1, ocr: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: '檔案損毀' }), { status: 422 }))
    vi.stubGlobal('fetch', f)
    const pdf1 = new File(['%PDF'], '一.pdf', { type: 'application/pdf' })
    const pdf2 = new File(['%PDF'], '二.pdf', { type: 'application/pdf' })
    const pdf3 = new File(['%PDF'], '三.pdf', { type: 'application/pdf' })
    await expect(uploadDocuments('c1', [pdf1, pdf2, pdf3], 'evidence')).rejects.toThrow(
      '「二.pdf」上傳失敗：檔案損毀（前 1 份已上傳）')
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('startDocumentOcr reports confirm_ocr as an ApiError code', async () => {
    const f = respond(409, { detail: { code: 'confirm_ocr', message: '要開始辨識嗎？' } })
    const err = await startDocumentOcr('d1', { confirm: false }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect([err.code, err.message]).toEqual(['confirm_ocr', '要開始辨識嗎？'])
    expect(f).toHaveBeenCalledWith('/api/documents/d1/ocr', expect.objectContaining({ method: 'POST',
      body: JSON.stringify({ confirm: false, retry_failed: false }) }))
  })

  it('downloads the filing package with the UTF-8 filename from the response', async () => {
    const f = vi.fn(async () => new Response('zip', { status: 200, headers: {
      'Content-Disposition': "attachment; filename=filing-package.zip; filename*=UTF-8''%E6%B0%91%E4%BA%8B%E8%B5%B7%E8%A8%B4%E7%8B%80_%E9%81%9E%E7%8B%80%E5%8C%85.zip" } }))
    vi.stubGlobal('fetch', f)
    const r = await downloadFilingPackage('dr1')
    expect(r.filename).toBe('民事起訴狀_遞狀包.zip')
    expect(r.blob.size).toBe(3)
    expect(f).toHaveBeenCalledWith('/api/drafts/dr1/filing-package', expect.objectContaining({ method: 'POST' }))
    vi.stubGlobal('fetch', vi.fn(async () => new Response('x', { status: 200 })))
    expect((await downloadFilingPackage('dr1')).filename).toBe('遞狀包.zip')
    respond(409, { blockers: [{ paragraph_seq: 2, reason: 'unreviewed' }] })
    await expect(downloadFilingPackage('dr1')).rejects.toBeInstanceOf(ExportBlockedError)
  })

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

  it('keeps the reason codes of a refused review on the ApiError', async () => {
    respond(409, { detail: { code: 'review_blocked', message: '尚有未解決的問題，無法標記已審',
      reasons: ['image_unverified:原證1'] } })
    const err = await reviewParagraph('p1', 'v1').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect([err.status, err.code, err.message, err.reasons]).toEqual([409, 'review_blocked',
      '尚有未解決的問題，無法標記已審', ['image_unverified:原證1']])
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

  it('turns a validation error list into readable text', async () => {
    respond(422, { detail: [{ loc: ['body', 'case_number'], msg: 'String should have at most 100 characters' }] })
    const err = await uploadDocument('c1', new File(['x'], 'a.txt'), 'evidence').catch((e) => e)
    expect(err.message).toBe('輸入資料有誤：String should have at most 100 characters')
  })
})
