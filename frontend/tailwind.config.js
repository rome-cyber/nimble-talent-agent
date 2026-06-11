/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        nimble: {
          black:      '#0A0A0A',
          ink:        '#1C1C1E',
          gold:       '#E8B84B',
          'gold-lt':  '#F5D06B',
          'gold-dk':  '#C49520',
        },
      },
      animation: {
        'fade-in-up':  'fade-in-up 0.4s cubic-bezier(0,0,0.2,1) both',
        'fade-in':     'fade-in 0.25s cubic-bezier(0,0,0.2,1) both',
        'scale-in':    'scale-in 0.25s cubic-bezier(0,0,0.2,1) both',
        'slide-down':  'slide-down 0.22s cubic-bezier(0,0,0.2,1) both',
        'shimmer':     'shimmer 1.6s linear infinite',
        'spin-slow':   'spin 1.4s linear infinite',
      },
      keyframes: {
        'fade-in-up': {
          '0%':   { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'scale-in': {
          '0%':   { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'slide-down': {
          '0%':   { opacity: '0', transform: 'translateY(-5px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      boxShadow: {
        'card':    '0 1px 3px 0 rgba(0,0,0,0.06), 0 1px 2px -1px rgba(0,0,0,0.04)',
        'card-md': '0 4px 12px 0 rgba(0,0,0,0.08), 0 1px 3px -1px rgba(0,0,0,0.05)',
        'gold':    '0 0 0 3px rgba(232,184,75,0.25)',
      },
    },
  },
  plugins: [],
}
