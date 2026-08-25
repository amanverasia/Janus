export type JsonValue = string | number | boolean | null | JsonValue[] | JsonObject;
export type JsonObject = { [key: string]: JsonValue | undefined };

export interface AlertItem {
  id?: string | number;
  level?: string;
  severity?: string;
  title?: string;
  message?: string;
}

export interface StateEnvelope {
  section: string;
  alerts: AlertItem[];
  data: JsonObject;
  meta: JsonObject;
}

export interface ToastItem {
  id: number;
  kind: 'success' | 'error' | 'info';
  message: string;
}

export interface MutationOptions {
  method?: 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: FormData | JsonObject;
  success?: string;
  refresh?: boolean;
}
