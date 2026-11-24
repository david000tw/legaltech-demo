import type { ToolStep } from '../api'

// 試算的逐步說明：項目、結果、依據
export default function StepList({ steps }: { steps: ToolStep[] }) {
  if (!steps.length) return null
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-left text-slate-500">
          <th className="py-1 pr-3 font-normal">項目</th>
          <th className="py-1 pr-3 font-normal">結果</th>
          <th className="py-1 font-normal">依據</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((s, i) => (
          <tr key={i} className="border-b border-slate-100 align-top">
            <td className="py-1 pr-3 text-slate-700">{s.label}</td>
            <td className="py-1 pr-3 text-slate-900">{s.value}</td>
            <td className="py-1 text-slate-600">{s.basis}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
