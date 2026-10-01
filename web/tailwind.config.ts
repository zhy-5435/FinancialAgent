import type { Config } from 'tailwindcss';

// 设计 token 对齐 codex1.html 示例：白底 + gray 灰阶 + blue-500 点缀
// 语义 token 名保持不变（base/surface/line/ink...），组件层无需感知配色切换
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#ffffff',      // 页面底色（MainContent bg-white）
        surface: '#f9fafb',   // 侧栏/卡片底色（gray-50）
        raised: '#f3f4f6',    // 悬浮/选中底色（gray-100）
        line: '#e5e7eb',      // 分隔线/边框（gray-200）
        ink: '#1f2937',       // 主文字（gray-800）
        shade: '#374151',     // 深色按钮 hover（gray-700）
        muted: '#4b5563',     // 次级文字（gray-600）
        faint: '#9ca3af',     // 弱化文字（gray-400）
        accent: '#3b82f6',    // 主交互色（blue-500）
        ok: '#10b981',
        warn: '#f59e0b',
        err: '#ef4444',
      },
      fontFamily: {
        // 示例同款系统字体栈
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto',
          '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif',
        ],
        mono: ['"JetBrains Mono"', 'Consolas', '"Courier New"', 'monospace'],
      },
      boxShadow: {
        // 输入条示例的 shadow-sm / hover shadow-md 由 Tailwind 内建提供
      },
    },
  },
  plugins: [],
} satisfies Config;
