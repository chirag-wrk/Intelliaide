/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        sidebar: {
          bg: '#1b1b1b',
          hover: '#2b2b2b',
          active: '#EE0000',
        },
        brand: {
          red: '#EE0000',
          redDark: '#C00000',
          redLight: '#FF3333',
          dark: '#151515',
          navy: '#1a1a1a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
