import { type ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  eyebrow?: string
  title?: string
  action?: ReactNode
  interactive?: boolean
  subtle?: boolean
}

export function Card({
  children,
  className = '',
  eyebrow,
  title,
  action,
  interactive = false,
  subtle = false,
}: CardProps) {
  const panelClass = subtle
    ? 'macos-card-subtle'
    : interactive
    ? 'macos-card-interactive'
    : 'macos-card'

  return (
    <section className={`${panelClass} p-4 sm:p-5 ${className}`}>
      {(eyebrow || title || action) && (
        <div className="mb-3.5 flex items-start justify-between gap-3">
          <div>
            {eyebrow && <p className="text-[10px] font-semibold tracking-wider uppercase text-slate-400">{eyebrow}</p>}
            {title && <h3 className="text-sm font-semibold tracking-tight text-white mt-0.5">{title}</h3>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
