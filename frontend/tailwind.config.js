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
        // macOS / Apple HIG Dark Mode Palette
        macos: {
          bg: '#0d0f14',
          sidebar: '#12151d',
          card: 'rgba(23, 27, 36, 0.68)',
          cardHover: 'rgba(30, 35, 48, 0.78)',
          toolbar: 'rgba(18, 21, 29, 0.85)',
          fill: 'rgba(255, 255, 255, 0.06)',
          fillHover: 'rgba(255, 255, 255, 0.1)',
          fillActive: 'rgba(255, 255, 255, 0.16)',
          separator: 'rgba(255, 255, 255, 0.08)',
          border: 'rgba(255, 255, 255, 0.1)',
          borderStrong: 'rgba(255, 255, 255, 0.16)',
        },
        apple: {
          blue: '#0a84ff',
          green: '#30d158',
          orange: '#ff9f0a',
          red: '#ff453a',
          purple: '#bf5af2',
          cyan: '#64d2ff',
          yellow: '#ffd60a',
          gray: '#8e8e93',
        },
        ev: {
          positive: '#30d158',
          negative: '#ff453a',
          neutral: '#ff9f0a',
        },
        tier: {
          premium: '#bf5af2',
          value: '#0a84ff',
          skip: '#8e8e93',
        },
      },
      boxShadow: {
        'macos-card': '0 4px 20px -2px rgba(0, 0, 0, 0.28), inset 0 1px 0 0 rgba(255, 255, 255, 0.08)',
        'macos-card-hover': '0 8px 24px -2px rgba(0, 0, 0, 0.36), inset 0 1px 0 0 rgba(255, 255, 255, 0.14)',
        'macos-window': '0 16px 48px -4px rgba(0, 0, 0, 0.45), inset 0 1px 0 0 rgba(255, 255, 255, 0.12)',
        'macos-popover': '0 12px 32px -4px rgba(0, 0, 0, 0.4)',
        'macos-btn': '0 1px 2px 0 rgba(0, 0, 0, 0.2)',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"SF Pro Display"',
          'Inter',
          'ui-sans-serif',
          'system-ui',
          'sans-serif'
        ],
        mono: ['SF Mono', 'JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
