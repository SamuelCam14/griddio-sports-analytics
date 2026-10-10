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
    ? 'glass-panel-subtle'
    : interactive
    ? 'glass-panel-interactive'
    : 'glass-panel'

  return (
    <section className={`${panelClass} p-4 sm:p-5 ${className}`}>
      {(eyebrow || title || action) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            {eyebrow && <p className="eyebrow-accent">{eyebrow}</p>}
            {title && <h3 className="section-title-sm mt-0.5">{title}</h3>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
