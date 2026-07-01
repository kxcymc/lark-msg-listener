export type ApiQueryValue = string | number | boolean | null | undefined;

export type ApiQueryParams = Record<string, ApiQueryValue | ApiQueryValue[]>;

export type HealthLevel = 'success' | 'warning' | 'danger' | 'info';

export interface ApiPathCheck {
  key: string;
  label?: string;
  path: string;
  exists?: boolean;
  readable?: boolean;
  writable?: boolean;
  status?: string;
  message?: string;
}

export interface ApiHealthCheck {
  key: string;
  label: string;
  level: HealthLevel;
  value?: string;
  description?: string;
}

export interface ApiHealthMetric {
  label: string;
  value: string | number;
  description?: string;
  level?: HealthLevel;
}

export interface DashboardHealthResponse {
  status?: string;
  // 后端 /api/health 的 checks 是一个布尔映射：bot_ready / index_db_readable / out_dir_readable / logs_dir_readable。
  checks?: Record<string, boolean>;
}

export interface DashboardApiConfig {
  enabled?: boolean;
  host?: string;
  port?: number;
  max_json_preview_bytes?: number;
  tail_log_lines?: number;
  sanitize_enabled?: boolean;
}

export interface DashboardConfigResponse {
  // 后端把整个 config.toml（脱敏后）放在 config 字段下。
  config?: {
    dashboard_api?: DashboardApiConfig;
    [key: string]: unknown;
  };
}
