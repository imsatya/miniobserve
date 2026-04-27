/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
        sans: ['Geist', 'Inter', '"DM Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        page: '#0b1020',
        surface: '#0f172a',
        inset: '#111b31',
        line: '#27324a',
        lineSoft: '#334155',
        ink: '#e6edf7',
        muted: '#9fb0cc',
        accent: '#7c6af7',
        'accent-dim': '#6758d6',
        green: '#22d3a0',
        red: '#fb7185',
        yellow: '#f7c948',
        text: '#e6edf7',
        'text-muted': '#9fb0cc',
      },
    },
  },
  plugins: [],
}
