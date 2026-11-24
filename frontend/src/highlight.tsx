import type { ReactNode } from 'react'

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// 把查詢字詞的每個出現處包成 <mark>（判決檢索結果反白）；空字詞忽略，英文不分大小寫
export function highlight(text: string, terms: string[]): ReactNode {
  const words = [...new Set(terms.map((t) => t.trim()).filter(Boolean))].sort((a, b) => b.length - a.length)
  if (!words.length) return text
  const re = new RegExp(`(${words.map(escape).join('|')})`, 'gi')
  return text.split(re).map((part, i) => (i % 2 === 1 ? <mark key={i} className="bg-yellow-200">{part}</mark> : part))
}
