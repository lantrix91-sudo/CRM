import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getBoard, assignLead, type Board, type Lead } from "./api";
import "./style.css";
import "./theme.css";

function App() {
  const [board, setBoard] = useState<Board | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [city, setCity] = useState<number | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [workers, setWorkers] = useState<Record<string, string>>({});
  async function reload() {
    setBusy(true);
    try { setBoard(await getBoard()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить доску"); }
    finally { setBusy(false); }
  }
  useEffect(() => { void reload(); }, []);
  useEffect(() => {
    const refresh = () => { if (!document.hidden && !busy) void reload(); };
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
  }, [busy]);
  async function assign(card: Lead) {
    if (card.assignment_notice && !reasons[card.key]?.trim()) {
      setError(`Заказ № ${card.id}: укажите причину возврата, например «Мастер не отвечает».`);
      document.getElementById(`return-reason-${card.id}`)?.focus();
      return;
    }
    setBusy(true); setError("");
    try { await assignLead(card, workers[card.key] ? Number(workers[card.key]) : undefined, card.assignment_notice ? reasons[card.key] ?? "" : undefined); await reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось назначить мастера"); }
    finally { setBusy(false); }
  }
  const search = query.trim().toLowerCase();
  const phoneDigits = (value: string) => {
    const digits = value.replace(/\D/g, "");
    return digits.length === 11 && digits.startsWith("8") ? "7" + digits.slice(1) : digits;
  };
  const phoneQuery = /^[+0-9() .-]+$/.test(search) ? phoneDigits(search) : "";
  const selectedCity = board?.cities.find(item => item.id === city)?.id ?? board?.cities[0]?.id;
  const cards = board?.leads.filter(card => card.city_id === selectedCity).filter(card =>
    [card.client_name, card.phone, card.title, card.service_name, card.employee_name ?? ""].some(value => value.toLowerCase().includes(search))
    || (phoneQuery.length > 0 && phoneDigits(card.phone).includes(phoneQuery))
  ) ?? [];
  return <>
    <div className="kanban-workspace"><div className="page-title"><div><p className="eyebrow">КЛИЕНТЫ И ЗАКАЗЫ</p><h1>Заказы</h1><p className="subtitle">От обращения до оплаты — вся работа на одной доске.</p></div>{board?.can_add && <a className="button primary" href="/workspace/lead/new/">+ Новый лид</a>}</div>
    <div className="city-filter" aria-label="Фильтр по городу">{board?.cities.map(item => <button key={item.id} className={selectedCity === item.id ? "city-tab active" : "city-tab"} aria-pressed={selectedCity === item.id} onClick={() => setCity(item.id)} title="Активные лиды и заказы: Новый, Назначен, В работе">{item.name} <span>{item.count}</span></button>)}{board && !board.cities.length && <p>Нет активных городов.</p>}</div>
    <div className="toolbar"><label className="search-label" htmlFor="board-search">Поиск по доске</label><input id="board-search" type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Клиент, телефон, услуга или мастер" /><span>{cards.length} карточек</span><button className="button secondary" disabled={busy} onClick={() => void reload()}>Обновить</button></div>
    <p className="feedback" aria-live="polite">{busy ? "Обновляем…" : "Статусы меняются по действиям: согласие клиента → назначение мастера → завершение → оплата. Автообновление каждые 15 секунд."}</p>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="board" aria-label="Канбан" aria-busy={busy}>{board?.columns.map(column => <section key={column.id} className={`column column-${column.id}`} data-status={column.id} aria-label={column.label}>
      <div className="column-heading"><span className="dot"/><h2>{column.label}</h2><span className="count">{cards.filter(c => c.status === column.id).length}</span></div>
      <div className="column-body">{cards.filter(c => c.status === column.id).map(card => <article className={`lead-card ${card.repeat_of_id ? "repeat-repair-card" : ""} ${card.overdue ? "waiting-overdue" : ""}`} key={card.key} data-card-key={card.key}>
        <div className="card-meta">{card.kind === "lead" ? "ЛИД" : "ЗАКАЗ"} № {card.id}</div>{card.repeat_of_id && <div className="repeat-repair-badge">Повторка · заказ № {card.repeat_of_id}</div>}<h3><a href={card.kind === "lead" ? `/workspace/lead/${card.id}/` : `/orders/${card.id}/`}>{card.client_name}</a></h3>{card.scheduled_at && <div className="appointment-badge">Запись: {new Date(card.scheduled_at).toLocaleString("ru-RU", { timeZone: "Asia/Qyzylorda", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</div>}<div className={`client-badge ${card.client_previous_count > 0 ? "client-returning" : "client-new"}`}>{card.client_previous_count > 0 ? `Повторный · ${card.client_previous_count}` : "Первое обращение"}</div><p className="phone">☎ {card.phone}</p><div className="service"><p>{card.service_name}</p></div><div className="assignee">Мастер: {card.employee_id !== null ? <span className={`worker-load worker-load-${card.employee_active_count >= 5 ? "red" : card.employee_active_count >= 3 ? "yellow" : "green"}`} title="Количество незакрытых заявок мастера">{card.employee_name} · {card.employee_active_count}</span> : "Не назначен"}</div>{card.waiting_minutes != null && <p>Ожидает мастера: {card.waiting_minutes} мин.{card.overdue && " · Требует внимания"}</p>}
        <details className="card-expand"><summary>Подробности и действия</summary><p>{card.title}</p><p className="source">Источник: {card.source || "Не указан"}</p><p>{card.detail}</p>{card.delivery && <p className="delivery-status">{card.delivery}</p>}<div className="card-actions">{board.can_change && card.kind === "lead" && card.status !== "lost" && <form method="post" action={`/workspace/lead/${card.id}/`}>
          <input type="hidden" name="csrfmiddlewaretoken" value={document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? ""}/><input type="hidden" name="action" value="convert"/><input type="hidden" name="return_to" value="kanban"/>
          <button className="button primary" disabled={busy}>Клиент согласился</button></form>}
        {board.can_change && card.kind === "order" && (card.status === "in_progress" || !!card.assignment_notice) && <>{!card.assignment_notice && <label className="worker-choice">Мастер<select aria-label={`Мастер для заказа ${card.id}`} value={workers[card.key] ?? ""} disabled={busy} onChange={e => setWorkers(current => ({...current, [card.key]: e.target.value}))}><option value="">Автоматический подбор</option>{board.workers?.filter(w => card.service_id !== null && w.service_ids.includes(card.service_id) && w.id !== card.employee_id).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}{card.assignment_notice && <label>Причина возврата (обязательно)<input id={`return-reason-${card.id}`} placeholder="Например: мастер не отвечает" aria-label={`Причина возврата заказа ${card.id}`} maxLength={150} value={reasons[card.key] ?? ""} onChange={e => setReasons(current => ({...current, [card.key]: e.target.value}))}/></label>}<button className="button secondary" disabled={busy} onClick={() => void assign(card)}>{card.assignment_notice ? "Вернуть оператору" : "Назначить мастера"}</button></>}
        {board.can_change && card.kind === "lead" && card.status !== "lost" && <details><summary>Закрыть как неудачную сделку</summary><form method="post" action={`/workspace/lead/${card.id}/`}><input type="hidden" name="csrfmiddlewaretoken" value={document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? ""}/><input type="hidden" name="action" value="lost"/><label>Причина отказа<input name="reason" required maxLength={300} placeholder="Например: клиент отказался от цены"/></label><button className="button secondary" disabled={busy}>Закрыть сделку</button></form></details>}
        {board.can_change && card.kind === "order" && (card.status === "in_progress" || card.status === "assigned") && <details><summary>Клиент отказался — отменить заказ</summary><form method="post" action={`/orders/${card.id}/`}><input type="hidden" name="csrfmiddlewaretoken" value={document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? ""}/><input type="hidden" name="action" value="cancel"/><input type="hidden" name="return_to" value="kanban"/><label>Причина отказа<input name="reason" required maxLength={300}/></label><button className="button secondary" disabled={busy}>Отменить заказ</button></form></details>}
        <a href={card.kind === "lead" ? `/workspace/lead/${card.id}/` : `/orders/${card.id}/`}>{card.kind === "lead" ? "Открыть обращение" : "Заказ и история"}</a>
        {card.status === "completed" && board.can_manage && <a className="button primary" href={`/orders/${card.id}/`}>Проверить оплату</a>}</div></details>
      </article>)}{!cards.some(c => c.status === column.id) && <p className="empty-column">Пока нет карточек</p>}</div>
    </section>)}</div></div></>;
}
createRoot(document.getElementById("root")!).render(<App/>);
