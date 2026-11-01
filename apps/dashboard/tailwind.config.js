/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#070b14',
        card: '#0d1322',
        cardBorder: '#18243c',
        cardHover: '#131d33',
        bossBlue: '#38bdf8',
        neonGreen: '#10b981',
        neonPurple: '#a855f7',
        neonPink: '#ec4899',
        neonYellow: '#f59e0b',
        neonCyan: '#06b6d4',
      },
      animation: {
        'spin-slow': 'spin 20s linear infinite',
        'pulse-subtle': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }
    },
  },
  plugins: [],
}
