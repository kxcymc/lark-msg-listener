import type { Config } from 'tailwindcss';
import animate from 'tailwindcss-animate';

// 设计规范语义 token 统一在此声明。
// shadcn/ui 颜色通过 HSL CSS 变量驱动（见 src/app/styles/globals.css），
// 与设计稿语义色保持同一套来源，避免出现两套样式系统。
const config: Config = {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '24px',
    },
    extend: {
      colors: {
        // ===== shadcn/ui 语义变量（HSL）=====
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        // ===== 设计规范状态语义色（与 shadcn 共存，用于业务状态表达）=====
        // 同一含义统一同一颜色，见 DASHBOARD_DEVELOPMENT_REPORT.md 配色语义。
        // 通过 HSL CSS 变量驱动（见 globals.css），随浅/深色主题切换以保证对比度。
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
          soft: 'hsl(var(--success-soft))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
          soft: 'hsl(var(--warning-soft))',
        },
        danger: {
          DEFAULT: 'hsl(var(--danger))',
          foreground: 'hsl(var(--danger-foreground))',
          soft: 'hsl(var(--danger-soft))',
        },
        info: {
          DEFAULT: 'hsl(var(--info))',
          foreground: 'hsl(var(--info-foreground))',
          soft: 'hsl(var(--info-soft))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
        xl: '12px',
      },
      spacing: {
        nav: '240px',
        topbar: '56px',
        drawer: '560px',
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'PingFang SC',
          'Microsoft YaHei',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'Menlo', 'monospace'],
      },
      fontSize: {
        // 设计规范字号层级
        h1: ['20px', { lineHeight: '30px', fontWeight: '600' }],
        h2: ['16px', { lineHeight: '24px', fontWeight: '600' }],
        metric: ['30px', { lineHeight: '38px', fontWeight: '700' }],
        body: ['14px', { lineHeight: '21px' }],
        caption: ['12px', { lineHeight: '18px' }],
        code: ['13px', { lineHeight: '20px' }],
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [animate],
};

export default config;
