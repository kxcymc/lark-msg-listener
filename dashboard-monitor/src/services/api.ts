import type { ApiQueryParams, ApiQueryValue } from '@/types';

const API_BASE_PATH = '/api';

export class ApiError extends Error {
  status: number;
  statusText: string;
  details: unknown;

  constructor(message: string, response: Response, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = response.status;
    this.statusText = response.statusText;
    this.details = details;
  }
}

interface ApiRequestOptions extends RequestInit {
  query?: ApiQueryParams;
}

function appendQueryParam(params: URLSearchParams, key: string, value: ApiQueryValue) {
  if (value === undefined || value === null) {
    return;
  }
  params.append(key, String(value));
}

function buildApiUrl(path: string, query?: ApiQueryParams) {
  const normalizedPath = path.startsWith('/api')
    ? path
    : `${API_BASE_PATH}${path.startsWith('/') ? path : `/${path}`}`;
  const url = new URL(normalizedPath, window.location.origin);

  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.forEach((item) => appendQueryParam(url.searchParams, key, item));
        return;
      }
      appendQueryParam(url.searchParams, key, value);
    });
  }

  return `${url.pathname}${url.search}`;
}

async function readResponsePayload(response: Response) {
  const contentType = response.headers.get('content-type') ?? '';

  if (contentType.includes('application/json')) {
    return response.json() as Promise<unknown>;
  }

  return response.text();
}

export async function requestJson<TResponse>(
  path: string,
  { query, headers, ...init }: ApiRequestOptions = {},
): Promise<TResponse> {
  const response = await fetch(buildApiUrl(path, query), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...headers,
    },
  });

  if (!response.ok) {
    const details = await readResponsePayload(response).catch(() => undefined);
    const message =
      typeof details === 'object' &&
      details !== null &&
      'message' in details &&
      typeof details.message === 'string'
        ? details.message
        : `请求失败：${response.status} ${response.statusText}`;
    throw new ApiError(message, response, details);
  }

  if (response.status === 204) {
    return undefined as TResponse;
  }

  return readResponsePayload(response) as Promise<TResponse>;
}

export function getJson<TResponse>(path: string, query?: ApiQueryParams) {
  return requestJson<TResponse>(path, { method: 'GET', query });
}
