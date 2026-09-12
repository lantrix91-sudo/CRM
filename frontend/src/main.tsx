import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getBoard, assignLead, type Board, type Lead } from "./api";
import "./style.css";

function App() {
  const [board, setBoard] = useState<Board | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
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
    setBusy(true); setError("");
    try { await assignLead(card, workers[card.key] ? Number(workers[card.key]) : undefined); await reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось назначить мастера"); }
    finally { setBusy(false); }
  }
  const cards = board?.leads.filter(card => [card.client_name, card.phone, card.title, card.service_name, card.employee_name ?? ""].some(value => value.toLowerCase().includes(query.toLowerCase()))) ?? [];
  return <><header className="topbar"><a className="brand" href="/"><span className="logo">C</span> CRM</a><nav><a href="/operator/">Обращения и заказы</a><a href="/calls/">Звонки</a><a href="/">Моя страница</a></nav></header>
    <main><div className="page-title"><div><p className="eyebrow">КЛИЕНТЫ И ЗАКАЗЫ</p><h1>Доска работы</h1><p className="subtitle">От обращения до оплаты — вся работа на одной доске.</p></div>{board?.can_add && <a className="button primary" href="/workspace/lead/new/">+ Новый лид</a>}</div>
    <div className="toolbar"><label className="search">Поиск по доске<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Клиент, телефон, услуга или мастер" /></label><span>{cards.length} карточек</span><button className="button secondary" disabled={busy} onClick={() => void reload()}>Обновить</button></div>
    <p className="feedback" aria-live="polite">{busy ? "Обновляем…" : "Статусы меняются по действиям: согласие клиента → назначение мастера → завершение → оплата. Автообновление каждые 15 секунд."}</p>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="board" aria-label="Канбан" aria-busy={busy}>{board?.columns.map(column => <section key={column.id} className={`column column-${column.id}`} data-status={column.id} aria-label={column.label}>
      <div className="column-heading"><span className="dot"/><h2>{column.label}</h2><span className="count">{cards.filter(c => c.status === column.id).length}</span></div>
      <div className="column-body">{cards.filter(c => c.status === column.id).map(card => <article className="lead-card" key={card.key} data-card-key={card.key}>
        <div className="card-meta">{card.kind === "lead" ? "ЛИД" : "ЗАКАЗ"} № {card.id}</div><h3>{card.client_name}</h3><p className="phone">☎ {card.phone}</p><div className="service"><p>{card.service_name}</p></div><p>{card.title}</p><p className="source">Источник: {card.source || "Не указан"}</p><div className="assignee">Мастер: {card.employee_name || "Не назначен"}</div><p>{card.detail}</p>
        <div className="card-actions">{board.can_change && card.kind === "lead" && <form method="post" action={`/workspace/lead/${card.id}/`}>
          <input type="hidden" name="csrfmiddlewaretoken" value={document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? ""}/><input type="hidden" name="action" value="convert"/><input type="hidden" name="return_to" value="kanban"/>
          <button className="button primary" disabled={busy}>Клиент согласился</button></form>}
        {board.can_change && card.kind === "order" && card.status === "in_progress" && <><label className="worker-choice">Мастер<select aria-label={`Мастер для заказа ${card.id}`} value={workers[card.key] ?? ""} disabled={busy} onChange={e => setWorkers(current => ({...current, [card.key]: e.target.value}))}><option value="">Автоматический подбор</option>{board.workers?.filter(w => card.service_id !== null && w.service_ids.includes(card.service_id)).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label><button className="button secondary" disabled={busy} onClick={() => void assign(card)}>Назначить мастера</button></>}
        <a href={card.kind === "lead" ? `/workspace/lead/${card.id}/` : `/orders/${card.id}/`}>{card.kind === "lead" ? "Открыть обращение" : "Заказ и история"}</a>
        {card.status === "completed" && board.can_manage && <a className="button primary" href={`/orders/${card.id}/`}>Проверить оплату</a>}</div>
      </article>)}{!cards.some(c => c.status === column.id) && <p className="empty-column">Пока нет карточек</p>}</div>
    </section>)}</div></main></>;
}
createRoot(document.getElementById("root")!).render(<App/>);
