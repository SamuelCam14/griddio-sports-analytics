/**
 * Zustand store — Estado global de la aplicación.
 * Gestiona: bankroll, picks guardados, tema y configuración.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Pick } from '@/services/api'

interface AppState {
  // ─── Bankroll ─────────────────────────────────────────────────────────────
  bankroll: number
  setBankroll: (amount: number) => void

  // ─── Picks guardados ──────────────────────────────────────────────────────
  savedPicks: Pick[]
  savePick: (pick: Pick) => void
  removePick: (gameId: number) => void
  clearPicks: () => void

  // ─── Sport activo ─────────────────────────────────────────────────────────
  activeSport: 'NBA' | 'NFL'
  setActiveSport: (sport: 'NBA' | 'NFL') => void

  // ─── Tema ─────────────────────────────────────────────────────────────────
  darkMode: boolean
  toggleDarkMode: () => void

  // ─── Kelly fraction configurada ───────────────────────────────────────────
  kellyFraction: number
  setKellyFraction: (fraction: number) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      bankroll: 1000,
      setBankroll: (amount) => set({ bankroll: amount }),

      savedPicks: [],
      savePick: (pick) => {
        const existing = get().savedPicks.find(p => p.game_id === pick.game_id)
        if (!existing) {
          set(state => ({ savedPicks: [...state.savedPicks, pick] }))
        }
      },
      removePick: (gameId) =>
        set(state => ({ savedPicks: state.savedPicks.filter(p => p.game_id !== gameId) })),
      clearPicks: () => set({ savedPicks: [] }),

      activeSport: 'NFL',
      setActiveSport: (sport) => set({ activeSport: sport }),

      darkMode: true,
      toggleDarkMode: () => set(state => ({ darkMode: !state.darkMode })),

      kellyFraction: 0.25,
      setKellyFraction: (fraction) => set({ kellyFraction: fraction }),
    }),
    {
      name: 'sports-analytics-store',
      version: 1,
      migrate: (persistedState) => ({
        ...(persistedState as Partial<AppState>),
        // NFL es el deporte activo del producto; NBA permanece fuera de temporada.
        activeSport: 'NFL',
      } as AppState),
      // Persistir solo estos campos en localStorage
      partialize: (state) => ({
        bankroll: state.bankroll,
        savedPicks: state.savedPicks,
        activeSport: state.activeSport,
        darkMode: state.darkMode,
        kellyFraction: state.kellyFraction,
      }),
    }
  )
)
