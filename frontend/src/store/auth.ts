/** 认证状态。 */

import { create } from 'zustand'
import { ApiError, authApi, getToken, setToken, AUTH_EXPIRED_EVENT } from '../api/client'
import type { User } from '../api/types'

interface AuthState {
  user: User | null
  loading: boolean
  error: string | null

  bootstrap: () => Promise<void>
  login: (username: string, password: string) => Promise<boolean>
  register: (username: string, password: string, displayName?: string) => Promise<boolean>
  changePassword: (currentPassword: string, newPassword: string) => Promise<boolean>
  logout: () => void
  isAdmin: () => boolean
  /** 本地是否持有令牌。用于「需鉴权」页面的前置判断，避免打必败请求。 */
  hasToken: () => boolean
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  loading: true,
  error: null,

  bootstrap: async () => {
    if (!getToken()) {
      set({ loading: false, user: null })
      return
    }
    try {
      const user = await authApi.me()
      set({ user, loading: false, error: null })
    } catch (err) {
      // 只有服务端明确说「这个令牌不认」（401）才清掉本地令牌。
      // 网络抖动、后端还没起来（连不上）等情况必须保留令牌：否则用户
      // 只是刷新一下页面就被登出，还得重新输一遍密码。
      const rejected = err instanceof ApiError && err.status === 401
      if (rejected) setToken(null)
      set({ user: null, loading: false, error: rejected ? '登录已过期，请重新登录' : null })
    }
  },

  login: async (username, password) => {
    set({ error: null })
    try {
      const res = await authApi.login(username, password)
      setToken(res.access_token)
      set({ user: res.user, error: null })
      return true
    } catch (err) {
      set({ error: err instanceof Error ? err.message : '登录失败' })
      return false
    }
  },

  register: async (username, password, displayName = '') => {
    set({ error: null })
    try {
      const res = await authApi.register(username, password, displayName)
      setToken(res.access_token)
      set({ user: res.user, error: null })
      return true
    } catch (err) {
      set({ error: err instanceof Error ? err.message : '注册失败' })
      return false
    }
  },

  changePassword: async (currentPassword, newPassword) => {
    set({ error: null })
    try {
      const user = await authApi.changePassword(currentPassword, newPassword)
      set({ user, error: null })
      return true
    } catch (err) {
      set({ error: err instanceof Error ? err.message : '修改密码失败' })
      return false
    }
  },

  logout: () => {
    setToken(null)
    set({ user: null, error: null })
  },

  isAdmin: () => get().user?.role === 'admin',

  hasToken: () => getToken() !== null,
}))

// 令牌被服务端拒绝时，同步清空本地登录态，让界面立刻反映「已登出」。
// 否则 user 仍留在 store 里，页面仍以为已登录（例如顶部栏照旧显示头像），
// 直到用户手动刷新才恢复一致——这类"看起来没掉线但其实掉线"最让人迷惑。
if (typeof window !== 'undefined') {
  window.addEventListener(AUTH_EXPIRED_EVENT, () => {
    useAuth.setState({ user: null, error: '登录已过期，请重新登录' })
  })
}
