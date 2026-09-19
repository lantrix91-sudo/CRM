export type Status = "new" | "in_progress" | "assigned" | "completed" | "paid" | "lost";
export type Lead = {
  scheduled_at?: string | null;
  repeat_of_id?: number | null;
  delivery?: string | null; waiting_minutes?: number | null; overdue?: boolean; assignment_notice?: string | null; key: string; kind: "lead" | "order"; detail: string; id: number; service_id: number | null; employee_id: number | null; title: string; client_name: string; client_previous_count: number; phone: string;
  service_name: string; employee_name: string | null; employee_active_count: number; source: string; status: Status; city_id: number; city_name: string;
};
export type Board = {
  workers?: { id: number; name: string; service_ids: number[]; city_ids: number[] }[];
  cities: { id: number; name: string; count: number }[];
  leads: Lead[]; columns: { id: Status; label: string }[];
  can_manage?: boolean; archived_count: number; can_change: boolean; can_add: boolean;
};

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (!response.ok) {
    if (response.status === 403) throw new Error("Нет доступа или сеанс истёк. Проверьте вход и права в админке.");
    if (response.status === 409) throw new Error("Другой сотрудник уже изменил статус. Доска обновлена.");
    if (response.status === 404) throw new Error("Лид больше не существует. Обновите доску.");
    throw new Error("Не удалось выполнить запрос. Проверьте соединение и повторите.");
  }
  return response.json() as Promise<T>;
}

export const getBoard = (city?: number) => request<Board>(`/api/leads/board/${city ? `?city=${city}` : ""}`);
export const saveStatus = (lead: Lead, status: Status) => request<{ id: number; status: Status }>(
  `/api/leads/${lead.id}/status/`,
  {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "",
    },
    body: JSON.stringify({ status, expected_status: lead.status }),
  },
);

export async function assignLead(lead: Lead, employeeId?: number, reason?: string): Promise<Lead> {
  const response = await fetch(`/api/orders/${lead.id}/assign/`, {
    method: "POST", credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "",
    },
    body: JSON.stringify({ ...(employeeId === undefined || reason !== undefined ? {} : { employee_id: employeeId }), ...(reason === undefined ? {} : { action: "return", reason, expected_notice: lead.assignment_notice }) }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось назначить сотрудника.");
  }
  return response.json() as Promise<Lead>;
}
