import { type ButtonHTMLAttributes, type ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'glow' | 'emerald-glow' | 'segmented' | 'icon' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  children: ReactNode
  className?: string
  active?: boolean
}

export function Button({
  variant = 'secondary',
  children,
  className = '',
  active = false,
  type = 'button',
  ...rest
}: ButtonProps) {
  let baseClass = ''

  switch (variant) {
    case 'primary':
    case 'glow':
    case 'emerald-glow':
      baseClass = 'macos-btn-primary'
      break
    case 'icon':
      baseClass = 'macos-btn-icon'
      break
    case 'segmented':
      baseClass = `macos-segmented-item ${active ? 'macos-segmented-item-active' : ''}`
      break
    case 'ghost':
      baseClass = 'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-slate-400 hover:text-white hover:bg-white/[0.06] transition-colors'
      break
    case 'secondary':
    default:
      baseClass = active ? 'macos-pill-nav-active' : 'macos-btn-secondary'
      break
  }

  return (
    <button type={type} className={`${baseClass} ${className}`} {...rest}>
      {children}
    </button>
  )
}
