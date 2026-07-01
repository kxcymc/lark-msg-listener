import { lazy, Suspense } from 'react';
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  type LoaderFunctionArgs,
} from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { GlobalError } from '@/app/GlobalError';
import { Skeleton } from '@/components/ui/skeleton';

// 页面懒加载，与 src/app/navigation.ts 的路由元信息一一对应。
const OverviewPage = lazy(() => import('@/pages/Overview'));
const RealtimeMonitorPage = lazy(() => import('@/pages/RealtimeMonitor'));
const RecordsPage = lazy(() => import('@/pages/Records'));
const RecordReplayPage = lazy(() => import('@/pages/RecordReplay'));
const DemandsPage = lazy(() => import('@/pages/Demands'));
const DevTasksPage = lazy(() => import('@/pages/DevTasks'));
const ReposPage = lazy(() => import('@/pages/Repos'));
const WeeklyReportsPage = lazy(() => import('@/pages/WeeklyReports'));
const ReportsExportPage = lazy(() => import('@/pages/ReportsExport'));
const SettingsPage = lazy(() => import('@/pages/Settings'));

function PageFallback() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-80 w-full" />
    </div>
  );
}

function withSuspense(node: React.ReactNode) {
  return <Suspense fallback={<PageFallback />}>{node}</Suspense>;
}

function requireReplayRecordId({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const recordId = url.searchParams.get('recordId')?.trim();
  if (!recordId) {
    throw new Response('缺少合法的 recordId 查询参数', {
      status: 400,
      statusText: 'Bad Request',
    });
  }
  return null;
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    errorElement: <GlobalError />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: 'overview', element: withSuspense(<OverviewPage />) },
      { path: 'realtime', element: withSuspense(<RealtimeMonitorPage />) },
      { path: 'records', element: withSuspense(<RecordsPage />) },
      {
        path: 'records/replay',
        loader: requireReplayRecordId,
        element: withSuspense(<RecordReplayPage />),
      },
      { path: 'demands', element: withSuspense(<DemandsPage />) },
      { path: 'dev-tasks', element: withSuspense(<DevTasksPage />) },
      { path: 'repos', element: withSuspense(<ReposPage />) },
      { path: 'weekly-reports', element: withSuspense(<WeeklyReportsPage />) },
      { path: 'reports-export', element: withSuspense(<ReportsExportPage />) },
      { path: 'settings', element: withSuspense(<SettingsPage />) },
      { path: '*', element: <Navigate to="/overview" replace /> },
    ],
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
