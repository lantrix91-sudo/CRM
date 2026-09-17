export type Session = { server: string; token: string; name: string };
export type Order = { id: number; title: string; service: string; status: string; status_label: string; is_free: boolean;
  repeat_of: number | null; client: string | null; phone: string | null; address: string; appliance_type: string;
  brand: string; comment: string; amount: string | null; expenses: string; work_comment: string; actions: string[];
  events?: { id: number; description: string; actor_name: string; created_at: string }[] };
export type Page = { results: Order[]; next: string | null; count: number };
export type Preview = { amount: string; expenses: string; net: string; worker_share: string | null;
  company_share: string | null; confirmation: string; comment: string };
export type Summary = { revenue: string; expenses: string; worker_amount: string | null; company_amount: string | null;
  order_count: number; balance_due?: string; transferred_all_time?: string };
export type Profile = { name: string; percentage: string | null; dashboard: { today: Summary; month: Summary; current_shift: Summary } };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }

export function serverURL(input: string) {
  const url = new URL(input.trim());
  const parts = url.hostname.split('.');
  const ipv4 = parts.length === 4 && parts.every(p => /^\d+$/.test(p) && Number(p) <= 255);
  const [a, b] = parts.map(Number);
  const local = url.hostname === 'localhost' || url.hostname === '[::1]' ||
    (ipv4 && (a === 127 || a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31)));
  const protocolAllowed = url.protocol === 'https:' || (__DEV__ && url.protocol === 'http:' && local);
  if (!protocolAllowed || url.username || url.password || url.search || url.hash || url.pathname !== '/')
    throw new Error(__DEV__
      ? 'Укажите HTTPS-сервер или локальный HTTP-адрес, например http://192.168.3.104:8000'
      : 'Укажите адрес HTTPS-сервера без пути, например https://crm.example.kz');
  return url.origin;
}
export async function api<T>(server: string, path: string, token?: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(serverURL(server) + '/api/mobile/' + path, {
      method: body === undefined ? 'GET' : 'POST', signal: controller.signal, redirect: 'error',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (response.status === 204) return undefined as T;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const text = data?.detail || (data ? Object.values(data).flat().join('\n') : 'Сервер недоступен');
      throw new ApiError(String(text), response.status);
    }
    if (data === null) throw new Error('Сервер вернул неверный ответ.');
    return data as T;
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('Сервер не ответил. Обновите данные перед повтором действия.');
    throw e;
  } finally { clearTimeout(timer); }
}
