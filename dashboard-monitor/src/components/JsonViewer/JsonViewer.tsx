import type { ReactNode } from 'react';
import { Braces, FileJson } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

interface JsonToken {
  value: string;
  type: 'key' | 'string' | 'number' | 'boolean' | 'null' | 'punctuation';
}

export interface JsonViewerProps {
  /** 待展示的数据 */
  data?: unknown;
  title?: ReactNode;
  description?: ReactNode;
  /** 原始文件路径提示 */
  filePath?: string;
  maxHeight?: number | string;
  actions?: ReactNode;
  className?: string;
}

const tokenClassName: Record<JsonToken['type'], string> = {
  key: 'text-primary',
  string: 'text-success',
  number: 'text-warning',
  boolean: 'text-info',
  null: 'text-muted-foreground',
  punctuation: 'text-foreground',
};

function stringifyJson(data: unknown) {
  if (data === undefined) return '';
  try {
    return JSON.stringify(
      data,
      (_key, value) => (typeof value === 'bigint' ? `${value.toString()}n` : value),
      2,
    );
  } catch (error) {
    return JSON.stringify(
      {
        error: 'JSON 序列化失败',
        message: error instanceof Error ? error.message : String(error),
      },
      null,
      2,
    );
  }
}

function tokenizeLine(line: string): JsonToken[] {
  const matches = line.match(
    /("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(?=\s*:)|"(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[[\]{},:])/g,
  );

  if (!matches) {
    return [{ value: line, type: 'punctuation' }];
  }

  const tokens: JsonToken[] = [];
  let cursor = 0;
  matches.forEach((match) => {
    const index = line.indexOf(match, cursor);
    if (index > cursor) {
      tokens.push({ value: line.slice(cursor, index), type: 'punctuation' });
    }

    let type: JsonToken['type'] = 'punctuation';
    if (match.startsWith('"')) {
      type = line.slice(index + match.length).trimStart().startsWith(':') ? 'key' : 'string';
    } else if (match === 'true' || match === 'false') {
      type = 'boolean';
    } else if (match === 'null') {
      type = 'null';
    } else if (/^-?\d/.test(match)) {
      type = 'number';
    }

    tokens.push({ value: match, type });
    cursor = index + match.length;
  });

  if (cursor < line.length) {
    tokens.push({ value: line.slice(cursor), type: 'punctuation' });
  }

  return tokens;
}

/**
 * JsonViewer JSON 查看器：等宽字体展示 + 语法高亮 + 滚动区 + 原始文件路径。
 */
export function JsonViewer({
  data,
  title = 'JSON 数据',
  description,
  filePath,
  maxHeight = 480,
  actions,
  className,
}: JsonViewerProps) {
  const text = stringifyJson(data);
  const lines = text.split('\n');
  const maxHeightValue = typeof maxHeight === 'number' ? `${maxHeight}px` : maxHeight;

  return (
    <div className={cn('min-w-0 max-w-full overflow-hidden rounded-lg border border-border bg-card shadow-sm', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border bg-muted/40 px-4 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Braces className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-body font-medium text-foreground">{title}</div>
            <div className="text-caption text-muted-foreground">
              {description ?? `${text ? lines.length : 0} 行，${text.length} 字符`}
            </div>
          </div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <ScrollArea style={{ maxHeight: maxHeightValue }}>
        {text ? (
          <pre className="overflow-x-auto p-4 font-mono text-code">
            {lines.map((line, lineIndex) => (
              <span key={`${lineIndex}-${line}`} className="block min-h-5 whitespace-pre">
                <span className="mr-4 inline-block w-8 select-none text-right text-muted-foreground/70">
                  {lineIndex + 1}
                </span>
                {tokenizeLine(line).map((token, tokenIndex) => (
                  <span
                    key={`${lineIndex}-${tokenIndex}-${token.value}`}
                    className={tokenClassName[token.type]}
                  >
                    {token.value}
                  </span>
                ))}
              </span>
            ))}
          </pre>
        ) : (
          <div className="flex min-h-[180px] flex-col items-center justify-center gap-2 p-6 text-center text-muted-foreground">
            <FileJson className="h-8 w-8" />
            <p className="text-body">暂无 JSON 数据</p>
          </div>
        )}
      </ScrollArea>
      {filePath ? (
        <div className="truncate border-t border-border bg-muted/30 px-4 py-2 font-mono text-caption text-muted-foreground">
          {filePath}
        </div>
      ) : null}
    </div>
  );
}
