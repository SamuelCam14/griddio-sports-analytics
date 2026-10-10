/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Paleta principal — oscuro con acentos verde/teal (EV positivo)
        brand: {
          50:  '#f0fdf4',
          100: '#dcfce7',
          200: '#bbf7d0',
          300: '#86efac',
          400: '#4ade80',
          500: '#22c55e',  // Verde principal
          600: '#16a34a',
          700: '#15803d',
          800: '#166534',
          900: '#14532d',
        },
        ev: {
          positive: '#22c55e',   // Verde — EV+
          negative: '#ef4444',   // Rojo  — EV-
          neutral:  '#f59e0b',   // Ámbar — Neutro
        },
        tier: {
          premium: '#a855f7',    // Púrpura — ★★★ PREMIUM
          value:   '#3b82f6',    // Azul    — ★★ VALUE
          skip:    '#6b7280',    // Gris    — ★ SKIP
        },
        surface: {
          dark: '#061310',
          panel: 'rgba(12, 26, 22, 0.78)',
          card: 'rgba(16, 34, 29, 0.72)',
          popover: 'rgba(9, 21, 18, 0.94)',
        },
        accent: {
          primary: '#10b981',
          glow: '#34d399',
          cyan: '#38bdf8',
          blue: '#3b82f6',
          violet: '#6366f1',
        },
      },
      boxShadow: {
        'glass': '0 8px 32px 0 rgba(0, 0, 0, 0.36), inset 0 1px 0 0 rgba(255, 255, 255, 0.08)',
        'glass-hover': '0 12px 40px 0 rgba(0, 0, 0, 0.45), inset 0 1px 0 0 rgba(255, 255, 255, 0.15)',
        'btn-glow': '0 0 24px -2px rgba(59, 130, 246, 0.6), 0 0 12px -1px rgba(99, 102, 241, 0.4)',
        'emerald-glow': '0 0 24px -2px rgba(16, 185, 129, 0.4)',
        'card-glow': '0 10px 40px -10px rgba(16, 185, 129, 0.15)',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
