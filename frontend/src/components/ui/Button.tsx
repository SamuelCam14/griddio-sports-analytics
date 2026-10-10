import { type ButtonHTMLAttributes, type ReactNode } from 'react'

export type ButtonVariant = 'glow' | 'emerald-glow' | 'glass' | 'pill' | 'icon' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  children: ReactNode
  className?: string
  active?: boolean
}

export function Button({
  variant = 'glass',
  children,
  className = '',
  active = false,
  type = 'button',
  ...rest
}: ButtonProps) {
  let baseClass = ''

  switch (variant) {
    case 'glow':
      baseClass = 'btn-glow-primary'
      break
    case 'emerald-glow':
      baseClass = 'btn-glow-emerald'
      break
    case 'icon':
      baseClass = 'btn-icon-glass'
      break
    case 'pill':
      baseClass = `glass-pill ${active ? 'glass-pill-active' : ''}`
      break
    case 'ghost':
      baseClass = 'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-slate-400 hover:text-white hover:bg-white/5 transition-colors'
      break
    case 'glass':
    default:
      baseClass = 'glass-pill'
      break
  }

  return (
    <button type={type} className={`${baseClass} ${className}`} {...rest}>
      {children}
    </button>
  )
}
