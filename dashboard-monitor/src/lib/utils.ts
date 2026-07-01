import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// 注册项目自定义字号（见 tailwind.config.ts fontSize），
// 否则 tailwind-merge 会把 text-caption/text-body 等误判为文字颜色，
// 与 text-warning/text-danger 等语义色冲突而被丢弃，导致状态徽标文字失色。
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      spacing: ['nav', 'topbar', 'drawer'],
    },
    classGroups: {
      'font-size': [{ text: ['h1', 'h2', 'metric', 'body', 'caption', 'code'] }],
    },
  },
});

/** 合并 Tailwind class，处理冲突类名。shadcn/ui 标准工具函数。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
