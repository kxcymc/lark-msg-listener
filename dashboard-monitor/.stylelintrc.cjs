module.exports = {
  extends: ['stylelint-config-standard-scss'],
  rules: {
    // 允许 Tailwind 指令
    'at-rule-no-unknown': [
      true,
      {
        ignoreAtRules: [
          'tailwind',
          'apply',
          'layer',
          'config',
          'screen',
          'variants',
          'responsive',
        ],
      },
    ],
    'scss/at-rule-no-unknown': [
      true,
      {
        ignoreAtRules: [
          'tailwind',
          'apply',
          'layer',
          'config',
          'screen',
          'variants',
          'responsive',
        ],
      },
    ],
    // 允许 shadcn CSS 变量命名（如 --primary-foreground）
    'custom-property-pattern': null,
    'selector-class-pattern': null,
    // CSS 变量值可能没有显式 fallback，关闭强约束
    'value-keyword-case': null,
  },
};
