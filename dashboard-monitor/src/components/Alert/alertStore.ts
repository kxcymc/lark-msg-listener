import { create } from 'zustand';

export type AlertTone = 'success' | 'error' | 'warning' | 'info';

export interface AlertItem {
  id: string;
  tone: AlertTone;
  title: string;
  description?: string;
  /** 自动消失时长（毫秒），传 0 则不自动消失 */
  duration: number;
}

export interface AlertOptions {
  title: string;
  description?: string;
  duration?: number;
}

interface AlertStore {
  alerts: AlertItem[];
  push: (tone: AlertTone, options: AlertOptions) => string;
  dismiss: (id: string) => void;
}

const DEFAULT_DURATION = 4000;

let seq = 0;
function nextId() {
  seq += 1;
  return `alert-${Date.now()}-${seq}`;
}

export const useAlertStore = create<AlertStore>((set) => ({
  alerts: [],
  push: (tone, { title, description, duration }) => {
    const id = nextId();
    set((state) => ({
      alerts: [
        ...state.alerts,
        { id, tone, title, description, duration: duration ?? DEFAULT_DURATION },
      ],
    }));
    return id;
  },
  dismiss: (id) => {
    set((state) => ({ alerts: state.alerts.filter((alert) => alert.id !== id) }));
  },
}));
