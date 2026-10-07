/**
 * BankrollWidget — Display compacto del bankroll con indicador de ROI.
 */
import { useState } from 'react'
import { useAppStore } from '@/store/useAppStore'

interface Props {
  bankroll: number
}

export function BankrollWidget({ bankroll }: Props) {
  const { setBankroll } = useAppStore()
  const [editing, setEditing] = useState(false)
  const [input, setInput] = useState(bankroll.toString())

  const handleSave = () => {
    const val = parseFloat(input)
    if (!isNaN(val) && val > 0) setBankroll(val)
    setEditing(false)
  }

  return (
    <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5">
      <span className="text-xs text-gray-500">Bankroll</span>
      {editing ? (
        <div className="flex items-center gap-1">
          <input
            autoFocus
            type="number"
            value={input}
            onChange={e => setInput(e.target.value)}
            onBlur={handleSave}
            onKeyDown={e => e.key === 'Enter' && handleSave()}
            className="w-24 bg-transparent text-brand-400 font-mono text-sm outline-none"
          />
        </div>
      ) : (
        <button
          onClick={() => { setInput(bankroll.toString()); setEditing(true) }}
          className="text-brand-400 font-mono font-bold text-sm hover:text-brand-300"
        >
          ${bankroll.toLocaleString()}
        </button>
      )}
      <span className="text-gray-600 text-xs">✏️</span>
    </div>
  )
}
