/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#1677ff', // NanZi Blue
          hover: '#4096ff',
          active: '#0958d9',
          dark: '#0958d9', // 添加dark变体，与active相同
        },
        sidebar: {
          DEFAULT: '#0b0f19', // Modern Slate Navy
          light: '#131b2e',
          border: 'rgba(255, 255, 255, 0.06)',
        }
      }
    },
  },
  plugins: [],
}
