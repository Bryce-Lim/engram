/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Tight"', 'system-ui', 'sans-serif'],
      },
      colors: {
        // Light bold orange accent over pure black + white. (Token kept named
        // `cyan` so existing `text-cyan` utilities recolor in one place.)
        cyan: '#ff9d3c',
        hair: 'rgba(255,255,255,0.12)',
        hair2: 'rgba(255,255,255,0.07)',
        mute: 'rgba(255,255,255,0.55)',
        faint: 'rgba(255,255,255,0.32)',
      },
      animation: {
        rise: 'rise 0.5s ease forwards',
        fadein: 'fadein 0.6s ease forwards',
      },
    },
  },
  plugins: [],
}
