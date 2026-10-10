import { type ReactNode } from 'react'

export type BadgeVariant =
  | 'live'
  | 'premium'
  | 'value'
  | 'neutral'
  | 'emerald'
  | 'cyan'
  | 'purple'
  | 'rose'

interface BadgeProps {
  variant?: BadgeVariant
  children: ReactNode
  className?: string
  pulse?: boolean
}

export function Badge({ variant = 'neutral', children, className = '', pulse }: BadgeProps) {
  if (variant === 'live') {
    return (
      <span className={`badge-live-pulse ${className}`}>
        <span className="badge-live-dot" />
        {children}
      </span>
    )
  }

  const variantStyles: Record<BadgeVariant, string> = {
    live: 'badge-live-pulse',
    premium: 'badge-tier-premium',
    value: 'badge-tier-value',
    neutral: 'badge-tier-neutral',
    emerald: 'border-emerald-400/30 bg-emerald-500/15 text-emerald-200',
    cyan: 'border-cyan-400/30 bg-cyan-500/15 text-cyan-200',
    purple: 'border-purple-400/30 bg-purple-500/15 text-purple-200',
    rose: 'border-rose-400/30 bg-rose-500/15 text-rose-200',
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase ${variantStyles[variant]} ${className}`}
    >
      {pulse && <span className="h-1.5 w-1.5 rounded-full bg-current animate-ping" />}
      {children}
    </span>
  )
}
