import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** 页面级错误边界：单个组件异常时保留可恢复入口，避免整页白屏。 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('界面渲染异常', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-[15px] font-medium">页面暂时无法显示</p>
        <p className="max-w-[460px] text-[12px] text-tertiary">
          {this.state.error.message || '发生了未预期的界面错误'}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="btn"
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => window.location.assign('/')}
            className="btn btn-primary"
          >
            返回首页
          </button>
        </div>
      </div>
    )
  }
}
