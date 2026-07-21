/** @type {import('tailwindcss').Config} */
//
// 设计 token 对齐 TDesign Chat（@tdesign-vue-next/chat）
// 值来源：npm 包 es/style/index.css 与 TDesign 通用 token 规范
// 核心三原则：
//   1. brand 色完全从气泡退场，只出现在 CTA / focus ring / loading 点
//   2. 气泡分层靠灰阶容器（container / secondarycontainer / specialcomponent），不靠阴影
//   3. 字体用 TDesign 中性无衬线，无衬线标题、无 uppercase 中文标签
//
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        // TDesign 中性无衬线栈，跨平台一致
        sans: ['"PingFang SC"', '"Microsoft YaHei"', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      colors: {
        primary: {
          // 全局 brand 保持 NanZi Blue 不变，对话页通过 --primary-color CSS 变量
          // 在根容器 scope override 为 TDesign 腾讯蓝 #0052D9
          DEFAULT: '#1677ff',
          hover: '#4096ff',
          active: '#0958d9',
          dark: '#0958d9',
        },
        sidebar: {
          DEFAULT: '#001529',
          light: '#002140',
        },
        // TDesign 灰阶容器三档：气泡分层靠它，不靠阴影
        tdContainer: {
          DEFAULT: '#FFFFFF', // AI 气泡底（亮色）
          dark: '#1F1F1F',    // AI 气泡底（暗色）
        },
        tdSecondaryContainer: {
          DEFAULT: '#F3F3F3', // 用户气泡底、思考块底（亮色）
          dark: '#2A2A2A',    // 暗色
        },
        tdSpecialComponent: {
          DEFAULT: '#F2F2F2', // 输入框底（亮色）
          dark: '#2C2C2C',    // 暗色
        },
      },
      borderRadius: {
        // TDesign 圆角阶梯：气泡复合 tail 用 radius-extraLarge(9px) + radius-medium(6px)
        'td-xl': '9px',
        'td-md': '6px',
      },
    },
  },
  plugins: [],
}
