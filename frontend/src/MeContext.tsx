// 目前登入者：由 App 的 RequireLogin 提供；測試沒有包 Provider 時是 null。
import { createContext, useContext } from 'react'
import type { Me } from './api'

export const MeContext = createContext<Me | null>(null)

export function useMe(): Me | null {
  return useContext(MeContext)
}
