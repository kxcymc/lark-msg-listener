import { useQuery } from '@tanstack/react-query';
import { getJson } from '@/services/api';
import { queryKeys } from '@/services/queryKeys';
import type { DashboardConfigResponse, DashboardHealthResponse } from '@/types';

const SETTINGS_REFETCH_INTERVAL_MS = 30 * 1000;

export function getDashboardHealth() {
  return getJson<DashboardHealthResponse>('/health');
}

export function getDashboardConfig() {
  return getJson<DashboardConfigResponse>('/config');
}

export function useDashboardHealthQuery() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: getDashboardHealth,
    refetchInterval: SETTINGS_REFETCH_INTERVAL_MS,
  });
}

export function useDashboardConfigQuery() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: getDashboardConfig,
    staleTime: SETTINGS_REFETCH_INTERVAL_MS,
  });
}
