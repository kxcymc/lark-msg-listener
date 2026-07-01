import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { EmptyState } from '@/components/EmptyState';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  /** 加载态展示骨架行 */
  loading?: boolean;
  /** 顶部标题区 */
  title?: ReactNode;
  description?: ReactNode;
  toolbar?: ReactNode;
  /** 空态定制 */
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: ReactNode;
  /** 骨架屏行数 */
  skeletonRows?: number;
  /** 行点击回调 */
  onRowClick?: (row: TData) => void;
  /** 是否启用分页，默认 true */
  pagination?: boolean;
  /** 每页条数，默认 10 */
  pageSize?: number;
  className?: string;
  tableClassName?: string;
}

/**
 * DataTable 数据表格：基于 TanStack Table 的基础渲染，提供排序、加载态、空态与行 hover。
 */
export function DataTable<TData, TValue>({
  columns,
  data,
  loading,
  title,
  description,
  toolbar,
  emptyTitle = '暂无数据',
  emptyDescription = '调整筛选条件或稍后刷新后再试',
  emptyAction,
  skeletonRows = 5,
  onRowClick,
  pagination = true,
  pageSize = 10,
  className,
  tableClassName,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [paginationState, setPaginationState] = useState<PaginationState>({
    pageIndex: 0,
    pageSize,
  });
  const table = useReactTable({
    data,
    columns,
    state: pagination ? { sorting, pagination: paginationState } : { sorting },
    onSortingChange: setSorting,
    onPaginationChange: pagination ? setPaginationState : undefined,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: pagination ? getPaginationRowModel() : undefined,
  });
  const hasHeader = Boolean(title ?? description ?? toolbar);
  const rowCount = table.getRowModel().rows.length;
  const pageCount = table.getPageCount();
  const currentPage = table.getState().pagination.pageIndex;
  const showPagination = pagination && !loading && data.length > 0;
  const rangeStart = data.length === 0 ? 0 : currentPage * paginationState.pageSize + 1;
  const rangeEnd = Math.min((currentPage + 1) * paginationState.pageSize, data.length);

  return (
    <div className={cn('overflow-hidden rounded-xl border border-border bg-card shadow-sm', className)}>
      {hasHeader ? (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border bg-card px-4 py-3">
          <div className="min-w-0">
            {title ? <h3 className="text-h2 text-foreground">{title}</h3> : null}
            {description ? (
              <p className="mt-0.5 text-caption text-muted-foreground">{description}</p>
            ) : null}
          </div>
          {toolbar ? <div className="flex shrink-0 items-center gap-2">{toolbar}</div> : null}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className={cn('w-full min-w-[640px] border-collapse text-body', tableClassName)}>
          <thead className="sticky top-0 z-10 bg-muted/70 backdrop-blur">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="h-11">
                {headerGroup.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sortState = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      className="border-b border-border px-4 text-left text-caption font-medium text-muted-foreground"
                      style={{ width: header.getSize() }}
                    >
                      {header.isPlaceholder ? null : (
                        <button
                          className={cn(
                            'inline-flex items-center gap-1.5 rounded-sm text-left transition-colors',
                            canSort
                              ? 'cursor-pointer hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2'
                              : 'cursor-default',
                          )}
                          disabled={!canSort}
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {canSort ? (
                            sortState === 'asc' ? (
                              <ArrowUp className="h-3.5 w-3.5" />
                            ) : sortState === 'desc' ? (
                              <ArrowDown className="h-3.5 w-3.5" />
                            ) : (
                              <ChevronsUpDown className="h-3.5 w-3.5 opacity-50" />
                            )
                          ) : null}
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: skeletonRows }).map((_, i) => (
                <tr key={i} className="h-12 border-t border-border">
                  {columns.map((_col, ci) => (
                    <td key={ci} className="px-4">
                      <Skeleton className={cn('h-4', ci === 0 ? 'w-32' : 'w-24')} />
                    </td>
                  ))}
                </tr>
              ))
            ) : rowCount ? (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  className={cn(
                    'h-12 border-t border-border transition-colors hover:bg-accent/60',
                    onRowClick && 'cursor-pointer',
                  )}
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={() => onRowClick?.(row.original)}
                  onKeyDown={(event) => {
                    if (!onRowClick) return;
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onRowClick(row.original);
                    }
                  }}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-4 py-3 align-middle text-foreground">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={columns.length}>
                  <EmptyState
                    action={emptyAction}
                    description={emptyDescription}
                    title={emptyTitle}
                  />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!loading ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-muted/30 px-4 py-2 text-caption text-muted-foreground">
          <span>
            {showPagination ? `第 ${rangeStart}-${rangeEnd} 条，共 ${data.length} 条` : `共 ${data.length} 条`}
          </span>
          <div className="flex items-center gap-3">
            {sorting.length ? (
              <span>已按 {sorting[0]?.id} {sorting[0]?.desc ? '降序' : '升序'} 排列</span>
            ) : (
              <span>默认排序</span>
            )}
            {showPagination && pageCount > 1 ? (
              <div className="flex items-center gap-1.5">
                <Button
                  aria-label="上一页"
                  className="h-7 w-7"
                  disabled={!table.getCanPreviousPage()}
                  size="icon"
                  type="button"
                  variant="outline"
                  onClick={() => table.previousPage()}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <span className="tabular-nums">
                  {currentPage + 1} / {pageCount}
                </span>
                <Button
                  aria-label="下一页"
                  className="h-7 w-7"
                  disabled={!table.getCanNextPage()}
                  size="icon"
                  type="button"
                  variant="outline"
                  onClick={() => table.nextPage()}
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
