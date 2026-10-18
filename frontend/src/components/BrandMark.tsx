/**
 * iMeeting 品牌标识。
 *
 * 构成：圆角底板 + 中心芯片 + 四向电路引脚 + 一道抽象信号弧（形似 C）。
 * 取「电子科技」与「连接 / 智能」的意象，做抽象化处理，不使用具体商标图形。
 *
 * 之所以做成组件而不是只放一张位图：顶栏、首页、登录页需要的尺寸不同
 * （24 / 40 / 56），用 SVG 才能在各种尺寸下都保持锐利。
 */
export function BrandMark({ size = 24, className }: { size?: number; className?: string }) {
  // 每个实例要有唯一 id，否则同页多个渐变会互相覆盖
  const gid = `imeeting-bg-${size}`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-label="iMeeting"
      className={className}
    >
      <defs>
        {/* 黑底（暗角）→ 红，呼应「白黑灰红」色系：黑为底、红为点缀 */}
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#1f1f1f" />
          <stop offset="100%" stopColor="#b81f1f" />
        </linearGradient>
      </defs>

      <rect width="32" height="32" rx="9" fill={`url(#${gid})`} />

      <path
        d="M22.8 8 A10.5 10.5 0 0 1 22.8 24"
        fill="none"
        stroke="#ffffff"
        strokeWidth="1.7"
        strokeLinecap="round"
        opacity="0.45"
      />

      <rect x="11" y="11" width="10" height="10" rx="3" fill="#ffffff" />
      <rect x="14" y="14" width="4" height="4" rx="1.2" fill="#d92b2b" />

      <g stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round">
        <path d="M16 11V7.6" />
        <path d="M16 21v3.4" />
        <path d="M11 16H7.6" />
        <path d="M21 16h3.4" />
      </g>
    </svg>
  )
}
