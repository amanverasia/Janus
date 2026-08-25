import type { JsonObject, MutationOptions, StateEnvelope } from './types';

let redirectingToLogin = false;

export interface ValidatedMutationOptions extends MutationOptions {
  validate?: (payload: unknown, response: Response) => unknown | Promise<unknown>;
}

function currentDashboardPath(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function redirectToLogin(): void {
  if (redirectingToLogin || window.location.pathname === '/dashboard/login') return;
  redirectingToLogin = true;
  const next = encodeURIComponent(currentDashboardPath());
  window.location.assign(`/dashboard/login?next=${next}`);
}

async function isLoginResponse(response: Response): Promise<boolean> {
  if (response.status === 401) return true;
  try {
    if (new URL(response.url, window.location.origin).pathname === '/dashboard/login') return true;
  } catch {
    return false;
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('text/html')) return false;
  const html = await response.clone().text();
  return (
    /<title>\s*Sign in - Janus\s*<\/title>/i.test(html) &&
    /<form[^>]+action=["']\/dashboard\/login["']/i.test(html)
  );
}

export async function dashboardFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  const response = await fetch(input, { credentials: 'same-origin', ...init });
  if (await isLoginResponse(response)) {
    redirectToLogin();
    throw new Error('Your dashboard session expired. Redirecting to sign in.');
  }
  return response;
}

export async function getState(section: string, signal?: AbortSignal): Promise<StateEnvelope> {
  if (section === 'not-found') return { section, alerts: [], data: {}, meta: {} };
  const query = window.location.search;
  const response = await dashboardFetch(
    `/dashboard/api/v2/state/${encodeURIComponent(section)}${query}`,
    {
      headers: { Accept: 'application/json' },
      signal
    }
  );
  if (!response.ok) throw new Error(await responseError(response));
  const raw: unknown = await response.json();
  const result = raw !== null && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  return {
    section: typeof result.section === 'string' ? result.section : section,
    alerts: Array.isArray(result.alerts) ? result.alerts : [],
    data:
      result.data !== null && typeof result.data === 'object' && !Array.isArray(result.data)
        ? (result.data as JsonObject)
        : {},
    meta:
      result.meta !== null && typeof result.meta === 'object' && !Array.isArray(result.meta)
        ? (result.meta as JsonObject)
        : {}
  };
}

export async function mutate(
  url: string,
  options: ValidatedMutationOptions = {}
): Promise<unknown> {
  const headers = new Headers({ Accept: 'application/json, text/plain, */*' });
  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    const hasFile = Array.from(options.body.values()).some((value) => value instanceof File);
    if (hasFile) {
      body = options.body;
    } else {
      body = new URLSearchParams(
        Array.from(options.body.entries()).map(([key, value]) => [key, String(value)])
      );
      headers.set('Content-Type', 'application/x-www-form-urlencoded;charset=UTF-8');
    }
  } else if (options.body) {
    body = JSON.stringify(options.body);
    headers.set('Content-Type', 'application/json');
  }
  const response = await dashboardFetch(url, {
    method: options.method ?? 'POST',
    body,
    headers
  });
  if (!response.ok) throw new Error(await responseError(response));
  const contentType = response.headers.get('content-type') ?? '';
  const payload: unknown = contentType.includes('application/json')
    ? await response.json()
    : await response.text();
  return options.validate ? options.validate(payload, response) : payload;
}

export async function responseError(response: Response): Promise<string> {
  const body = (await response.text())
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return body.slice(0, 320) || `${response.status} ${response.statusText}`;
}
