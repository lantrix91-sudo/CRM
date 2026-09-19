export type Session = {
  server: string;
  token: string;
  name: string;
};

export type Order = {
  id: number;
  title: string;
  service: string;
  status: string;
  status_label: string;
  deferred_until: string | null;
  scheduled_at?: string | null;
  is_free: boolean;

  repeat_of: number | null;

  client: string | null;
  phone: string | null;

  city: string;
  address: string;

  appliance_type: string;
  brand: string;

  comment: string;

  amount: string | null;
  expenses: string;
  work_comment: string;

  actions: string[];

  events?: {
    id: number;
    description: string;
    actor_name: string;
    created_at: string;
  }[];
};

export type Page = {
  results: Order[];
  next: string | null;
  count: number;
};

export type Preview = {
  amount: string;
  expenses: string;
  net: string;

  worker_share: string | null;
  company_share: string | null;

  confirmation: string;
  comment: string;
};

export type Summary = {
  revenue: string;
  expenses: string;

  worker_amount: string | null;
  company_amount: string | null;

  order_count: number;

  balance_due?: string;
  transferred_all_time?: string;
};

export type Profile = {
  name: string;
  percentage: string | null;

  dashboard: {
    today: Summary;
    month: Summary;
    current_shift: Summary;
  };
};


/* =========================================================
   API ERROR
   ========================================================= */

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);

    this.name = 'ApiError';
  }
}


/* =========================================================
   AUTH HANDLER

   App.tsx зарегистрирует здесь функцию logout().
   Если сервер вернёт 401 — она будет вызвана автоматически.
   ========================================================= */

let unauthorizedHandler: (() => void | Promise<void>) | null = null;


/**
 * App.tsx передаёт сюда функцию выхода.
 */
export function setUnauthorizedHandler(
  handler: (() => void | Promise<void>) | null
) {
  unauthorizedHandler = handler;
}


/* =========================================================
   SERVER URL
   ========================================================= */

export function serverURL(input: string) {
  const url = new URL(input.trim());

  const parts = url.hostname.split('.');

  const ipv4 =
    parts.length === 4 &&
    parts.every(
      (part) =>
        /^\d+$/.test(part) &&
        Number(part) >= 0 &&
        Number(part) <= 255
    );

  const [a, b] = parts.map(Number);

  const local =
    url.hostname === 'localhost' ||
    url.hostname === '[::1]' ||
    (
      ipv4 &&
      (
        a === 127 ||
        a === 10 ||
        (a === 192 && b === 168) ||
        (a === 172 && b >= 16 && b <= 31)
      )
    );

  const protocolAllowed =
    url.protocol === 'https:' ||
    (
      __DEV__ &&
      url.protocol === 'http:' &&
      local
    );

  if (
    !protocolAllowed ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== '/'
  ) {
    throw new Error(
      __DEV__
        ? 'Укажите HTTPS-сервер или локальный HTTP-адрес, например http://192.168.3.104:8000'
        : 'Укажите адрес HTTPS-сервера без пути, например https://crm.example.kz'
    );
  }

  return url.origin;
}


/* =========================================================
   API
   ========================================================= */

export async function api<T>(
  server: string,
  path: string,
  token?: string,
  body?: unknown
): Promise<T> {

  const controller = new AbortController();

  const timer = setTimeout(() => {
    controller.abort();
  }, 20000);

  try {

    const response = await fetch(
      serverURL(server) + '/api/mobile/' + path,
      {
        method: body === undefined ? 'GET' : 'POST',

        signal: controller.signal,

        redirect: 'error',

        headers: {
          'Content-Type': 'application/json',

          ...(token
            ? {
                Authorization: 'Bearer ' + token,
              }
            : {}),
        },

        ...(body === undefined
          ? {}
          : {
              body: JSON.stringify(body),
            }),
      }
    );


    /* =====================================================
       Нет контента
       ===================================================== */

    if (response.status === 204) {
      return undefined as T;
    }


    /* =====================================================
       Читаем ответ
       ===================================================== */

    const data = await response
      .json()
      .catch(() => null);


    /* =====================================================
       401 — пользователь не авторизован

       Например:
       - токен истёк
       - токен удалён
       - пользователь удалён
       - токен неправильный

       Автоматически вызываем logout()
       ===================================================== */

    if (response.status === 401) {

      if (unauthorizedHandler) {
        try {
          await unauthorizedHandler();
        } catch (error) {
          console.error(
            'Ошибка при автоматическом выходе:',
            error
          );
        }
      }

      const message =
        data?.detail ||
        'Сессия завершена. Войдите снова.';

      throw new ApiError(
        String(message),
        401
      );
    }


    /* =====================================================
       Остальные ошибки сервера
       ===================================================== */

    if (!response.ok) {

      const text =
        data?.detail ||
        (
          data
            ? Object.values(data)
                .flat()
                .join('\n')
            : 'Сервер недоступен'
        );

      throw new ApiError(
        String(text),
        response.status
      );
    }


    /* =====================================================
       Сервер должен вернуть JSON
       ===================================================== */

    if (data === null) {
      throw new Error(
        'Сервер вернул неверный ответ.'
      );
    }


    return data as T;

  } catch (error) {

    /* =====================================================
       Timeout
       ===================================================== */

    if (
      error instanceof Error &&
      error.name === 'AbortError'
    ) {
      throw new Error(
        'Сервер не ответил. Обновите данные перед повтором действия.'
      );
    }

    throw error;

  } finally {

    clearTimeout(timer);

  }
}