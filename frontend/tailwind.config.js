/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      screens: {
        xs: '480px',
      },
      spacing: {
        4.5: '1.125rem',
      },
      boxShadow: {
        '2xs': '0 1px rgb(0 0 0 / 0.05)',
        xs: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
      },
      backdropBlur: {
        xs: '2px',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'fade-in-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-up': {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shake: {
          '0%, 100%': { transform: 'translateX(0)' },
          '20%, 60%': { transform: 'translateX(-4px)' },
          '40%, 80%': { transform: 'translateX(4px)' },
        },
        'slide-in-right': {
          '0%': { opacity: '0', transform: 'translateX(100%)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'fade-slide-up': {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.2s ease-out both',
        'fade-in-up': 'fade-in-up 0.25s ease-out both',
        'slide-up': 'slide-up 0.25s cubic-bezier(0.16, 1, 0.3, 1) both',
        shake: 'shake 0.35s ease-in-out both',
        'slide-in-right': 'slide-in-right 0.25s cubic-bezier(0.16, 1, 0.3, 1) both',
        'fade-slide-up': 'fade-slide-up 0.25s ease-out both',
      },
      colors: {
        primary: {
          DEFAULT: '#1677ff', // NanZi Blue
          hover: '#4096ff',
          active: '#0958d9',
          dark: '#0958d9', // 添加dark变体，与active相同
          // 深色底上的主色文字/链接：DEFAULT(#1677ff) 与 hover(#4096ff) 在深蓝底上
          // 对比度只有 1.3~4.5:1，肉眼几乎读不出来；这一档在 #212d44 上约 7.5:1。
          bright: '#8ec5ff',
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
