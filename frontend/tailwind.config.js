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
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
