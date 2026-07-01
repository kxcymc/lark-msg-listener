import { useRouteError, isRouteErrorResponse, Link } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * 全局错误页 / 数据源断开告警（对应 00-global-error.svg）。
 * 路由错误边界，后续可扩展数据源断开顶部告警条。
 */
export function GlobalError() {
  const error = useRouteError();

  let message = '页面发生未知错误';
  if (isRouteErrorResponse(error)) {
    const description =
      typeof error.data === 'string' && error.data.trim()
        ? error.data
        : error.statusText;
    message = `${error.status} · ${description}`;
  } else if (error instanceof Error) {
    message = error.message;
  }

  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-background">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-danger-soft">
        <AlertTriangle className="h-7 w-7 text-danger" />
      </div>
      <h1 className="text-h1 text-foreground">出错了</h1>
      <p className="text-body text-muted-foreground">{message}</p>
      <Button asChild>
        <Link to="/overview">返回总览</Link>
      </Button>
    </div>
  );
}
