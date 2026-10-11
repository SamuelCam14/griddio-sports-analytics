import { useEffect, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title?: string
  eyebrow?: string
  children: ReactNode
  maxWidth?: 'md' | 'lg' | 'xl' | '2xl' | '4xl' | '6xl'
}

export function Modal({
  isOpen,
  onClose,
  title,
  eyebrow,
  children,
  maxWidth = '4xl',
}: ModalProps) {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    if (isOpen) {
      document.body.style.overflow = 'hidden'
      window.addEventListener('keydown', handleKeyDown)
    }
    return () => {
      document.body.style.overflow = ''
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, onClose])

  const widthClasses = {
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    '2xl': 'max-w-2xl',
    '4xl': 'max-w-4xl',
    '6xl': 'max-w-6xl',
  }

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5">
          {/* Backdrop blur */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/60 backdrop-blur-md"
            aria-hidden="true"
          />

          {/* Modal Container — macOS Sheet Style */}
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 10 }}
            transition={{ type: 'spring', damping: 28, stiffness: 350 }}
            className={`relative z-10 flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl border border-white/[0.12] bg-[#141720]/95 shadow-macos-window backdrop-blur-2xl ${widthClasses[maxWidth]}`}
            role="dialog"
            aria-modal="true"
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-3.5">
              <div>
                {eyebrow && <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{eyebrow}</p>}
                {title && <h2 className="text-sm font-semibold text-white mt-0.5">{title}</h2>}
              </div>
              <button
                type="button"
                onClick={onClose}
                className="grid h-7 w-7 place-items-center rounded-lg border border-white/[0.08] bg-white/[0.05] text-slate-400 hover:bg-white/[0.1] hover:text-white transition-colors"
                aria-label="Cerrar modal"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="custom-scrollbar overflow-y-auto p-5">{children}</div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}
