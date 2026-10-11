import { type ReactNode } from 'react'

export type BadgeVariant =
  | 'live'
  | 'premium'
  | 'value'
  | 'neutral'
  | 'success'
  | 'warning'
  | 'danger'
  | 'blue'

interface BadgeProps {
  variant?: BadgeVariant
  children: ReactNode
  className?: string
  pulse?: boolean
}

export function Badge({ variant = 'neutral', children, className = '', pulse }: BadgeProps) {
  if (variant === 'live') {
    return (
      <span className={`macos-badge-live ${className}`}>
        <span className="macos-badge-live-dot" />
        {children}
      </span>
    )
  }

  const variantStyles: Record<BadgeVariant, string> = {
    live: 'macos-badge-live',
    premium: 'border-apple-purple/25 bg-apple-purple/15 text-[#d896ff]',
    value: 'border-apple-blue/25 bg-apple-blue/15 text-[#70b4ff]',
    neutral: 'border-white/[0.08] bg-white/[0.05] text-slate-300',
    success: 'border-apple-green/25 bg-apple-green/15 text-[#54e378]',
    warning: 'border-apple-orange/25 bg-apple-orange/15 text-[#ffb340]',
    danger: 'border-apple-red/25 bg-apple-red/15 text-rose-300',
    blue: 'border-apple-blue/25 bg-apple-blue/15 text-[#70b4ff]',
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-medium tracking-wide ${variantStyles[variant]} ${className}`}
    >
      {pulse && <span className="h-1.5 w-1.5 rounded-full bg-current animate-ping" />}
      {children}
    </span>
  )
}
