import { useMemo } from 'react';
import { useAlertStore, type AlertOptions } from './alertStore';

/**
 * useAlert：全局动作反馈入口。所有接口请求的成功/失败/警告提示统一从这里发出，
 * 禁止页面内部再自行实现一套 toast / 成功条幅。
 */
export function useAlert() {
  const push = useAlertStore((state) => state.push);
  const dismiss = useAlertStore((state) => state.dismiss);

  return useMemo(
    () => ({
      success: (options: AlertOptions) => push('success', options),
      error: (options: AlertOptions) => push('error', options),
      warning: (options: AlertOptions) => push('warning', options),
      info: (options: AlertOptions) => push('info', options),
      dismiss,
    }),
    [push, dismiss],
  );
}
