import { describe, expect, it } from 'vitest'
import { attorneyProblem, splitAttorneys } from './caseForms'

describe('attorneys', () => {
  it('splits on commas, semicolons and 頓號', () => {
    expect(splitAttorneys('王大明律師，李小華律師；陳一律師、趙四律師,錢五律師;孫六律師'))
      .toEqual(['王大明律師', '李小華律師', '陳一律師', '趙四律師', '錢五律師', '孫六律師'])
    expect(splitAttorneys('  ')).toEqual([])
  })

  it('keeps a name with spaces as one attorney', () => {
    // 最終審查：以空白分隔會把「王大明 律師」拆成兩位，狀頭印出「訴訟代理人　律師」
    expect(splitAttorneys('王大明 律師， David Wang律師 ')).toEqual(['王大明 律師', 'David Wang律師'])
  })

  it('reports too many or too long names', () => {
    expect(attorneyProblem(Array.from({ length: 10 }, (_, i) => `律師${i}`).join('，'))).toBeNull()
    expect(attorneyProblem(Array.from({ length: 11 }, (_, i) => `律師${i}`).join('，'))).toBe('訴訟代理人最多 10 位')
    expect(attorneyProblem('甲'.repeat(101))).toBe('訴訟代理人每位最多 100 字')
  })
})
