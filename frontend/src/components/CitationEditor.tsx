// 段落內文的編輯框（設計 5.3）：出處顯示成〔被證1第1頁〕這樣不能拆開的一整塊，只能整塊刪除或剪下貼上搬動；
// 存檔時轉回原本的 {{cite:X}}／{{quote:X}} 標記。用 TipTap（ProseMirror，MIT），注音輸入由它處理。
import { Node, mergeAttributes, type Editor } from '@tiptap/core'
import Document from '@tiptap/extension-document'
import Paragraph from '@tiptap/extension-paragraph'
import Text from '@tiptap/extension-text'
import { EditorContent, useEditor } from '@tiptap/react'
import { useEffect, useRef } from 'react'
import type { CitationView } from '../api'
import { fromEditorDoc, markerLabel, sanitizePasted, toEditorDoc, type EditorDoc } from '../lib/markers'

type LabelFor = (kind: 'cite' | 'quote', code: string) => string

const Citation = Node.create<{ labelFor: LabelFor }>({
  name: 'citation',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  addOptions() {
    return { labelFor: (_kind, code) => `〔${code}〕` }
  },
  addAttributes() {
    return {
      kind: { default: 'cite', parseHTML: (el) => el.getAttribute('data-kind') },
      code: { default: '', parseHTML: (el) => el.getAttribute('data-code') },
    }
  },
  parseHTML() {
    return [{ tag: 'span[data-citation]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    const { kind, code } = node.attrs as { kind: 'cite' | 'quote'; code: string }
    return ['span', mergeAttributes(HTMLAttributes, {
      'data-citation': '', 'data-kind': kind, 'data-code': code, contenteditable: 'false',
      class: 'mx-0.5 rounded bg-sky-100 px-1 text-sky-800 select-all',
    }), this.options.labelFor(kind, code)]
  },
  renderText({ node }) {
    return this.options.labelFor(node.attrs.kind, node.attrs.code)
  },
})

export default function CitationEditor({ value, citations, onChange, ariaLabel, onEditor }: {
  value: string
  citations: CitationView[]
  onChange: (v: string) => void
  ariaLabel: string
  onEditor?: (editor: Editor) => void  // 測試用：拿到編輯器實體
}) {
  const latest = useRef(onChange)
  useEffect(() => { latest.current = onChange })
  // 編輯期間引用清單不會變（新增出處要用「要求重寫」），所以標籤用打開編輯框時的清單
  const editor = useEditor({
    extensions: [Document, Paragraph, Text, Citation.configure({
      labelFor: (kind, code) => markerLabel({ type: 'marker', kind, code }, citations),
    })],
    content: toEditorDoc(value),
    editorProps: {
      attributes: {
        'aria-label': ariaLabel, role: 'textbox', 'aria-multiline': 'true',
        class: 'mt-1 min-h-32 w-full whitespace-pre-wrap rounded border border-slate-300 p-2 text-sm',
      },
      transformPastedText: sanitizePasted,
      transformPastedHTML: sanitizePasted,
    },
    onUpdate: ({ editor: e }) => latest.current(fromEditorDoc(e.getJSON() as EditorDoc)),
  })
  useEffect(() => { if (editor && onEditor) onEditor(editor) }, [editor, onEditor])
  return <EditorContent editor={editor} />
}
