import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState, BackHandler, KeyboardAvoidingView, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { api, type Session } from './api';

import { Button } from './components/Button';
import { Field } from './components/Field';
import { ui } from './theme';
import { useTask } from './hooks/useTask';

type Tab = 'dashboard' | 'leads' | 'orders' | 'clients' | 'team';
type Choice = { id: number; name: string };
type Options = { cities: Choice[]; services: Choice[]; workers: (Choice & { available: boolean; service_ids: number[]; city_ids: number[] })[] };
type Row = {
  id: number; kind?: 'lead' | 'order'; name: string; phone?: string; address?: string;
  city?: string; city_id?: number | null; service?: string; service_id?: number | null;
  status?: string; status_label?: string; employee?: string; employee_id?: number | null;
  comment?: string; work_comment?: string; amount?: string | null; source?: string;
  appliance_type?: string; brand?: string; scheduled_at?: string | null; assignment_notice?: string | null;
  active?: boolean; available?: boolean; active_orders?: number; cities?: string[];
  lead_count?: number; order_count?: number;
  rates?: { service: string; percentage: string; active: boolean }[];
  events?: { id: number; description: string; actor_name: string; created_at: string }[];
};
type Page = { results: Row[]; next: string | null; count: number };
type Dashboard = { date: string; paid_count: number; revenue: string; expenses: string; unassigned: number; active_orders: number; new_leads: number };
type Draft = { client_name: string; client_phone: string; client_address: string; city: string; service: string; comment: string; source: string; scheduled_at: string; appliance_type: string; brand: string };
const blank: Draft = { client_name: '', client_phone: '', client_address: '', city: '', service: '', comment: '', source: '', scheduled_at: '', appliance_type: '', brand: '' };
const labels: Record<Tab, string> = { dashboard: 'Главная', leads: 'Лиды', orders: 'Заказы', clients: 'Клиенты', team: 'Команда' };
const money = (value: string) => `${Number(value).toLocaleString('ru-RU')} ₸`;
const date = (value: string) => new Date(value).toLocaleString('ru-RU', { timeZone: 'Asia/Qyzylorda' });
function inputDate(value?: string | null) {
  if (!value) return '';
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Qyzylorda', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value));
  const get = (key: string) => parts.find(p => p.type === key)?.value;
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}
function Choices({ title, items, value, onChange }: { title: string; items: Choice[]; value: string; onChange: (v: string) => void }) {
  return <View style={styles.field}><Text style={styles.label}>{title}</Text><View style={styles.wrap}>{items.map(item => <Pressable accessibilityRole="button" accessibilityState={{ selected: value === String(item.id) }} key={item.id} onPress={() => onChange(String(item.id))} style={[styles.chip, value === String(item.id) && styles.selected]}><Text style={value === String(item.id) ? styles.selectedText : styles.label}>{item.name}</Text></Pressable>)}</View>{items.length === 0 && <Text style={styles.muted}>Нет доступных вариантов</Text>}</View>;
}

export default function StaffWorkspace({ session, onLogout }: { session: Session; onLogout: () => Promise<void> }) {
  const manager = session.role === 'manager';
  const [tab, setTab] = useState<Tab>(manager ? 'dashboard' : 'leads');
  const [rows, setRows] = useState<Row[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [options, setOptions] = useState<Options>({ cities: [], services: [], workers: [] });
  const [selected, setSelected] = useState<Row | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(blank);
  const [employee, setEmployee] = useState('');
  const [reason, setReason] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [history, setHistory] = useState(false);
  const [page, setPage] = useState(1);
  const [next, setNext] = useState(false);
  const [count, setCount] = useState(0);
  const { busy, error, setError, run } = useTask(onLogout);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const request = useCallback(<T,>(path: string, body?: unknown) => api<T>(session.server, `staff/${path}`, session.token, body), [session]);
  const load = useCallback(async (pageNumber = 1) => {
    if (tab === 'dashboard') { const data = await request<Dashboard>('dashboard/'); if (alive.current) setDashboard(data); return; }
    const data = await request<Page>(`${tab}/?page=${pageNumber}&scope=${history ? 'history' : 'active'}&q=${encodeURIComponent(query)}`);
    if (!alive.current) return;
    setRows(old => pageNumber === 1 ? data.results : [...old, ...data.results]);
    setPage(pageNumber); setNext(Boolean(data.next)); setCount(data.count);
  }, [tab, history, query, request]);
  useEffect(() => { void run(async () => { const data = await request<Options>('options/'); if (alive.current) setOptions(data); await load(); }); }, [load, request]);
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { if (busy) return true; if (editing) { setEditing(false); return true; } if (selected) { setSelected(null); return true; } return false; });
    return () => sub.remove();
  }, [busy, editing, selected]);
  const refresh = () => run(async () => {
    if (editing) return;
    if (selected?.kind) setSelected(await request<Row>(`${selected.kind === 'lead' ? 'leads' : 'orders'}/${selected.id}/`));
    else await load();
  });
  useEffect(() => { const sub = AppState.addEventListener('change', state => { if (state === 'active' && !editing) void refresh(); }); return () => sub.remove(); }, [load, selected, editing]);
  const open = (row: Row) => void run(async () => {
    if (!row.kind) return;
    setSelected(await request<Row>(`${row.kind === 'lead' ? 'leads' : 'orders'}/${row.id}/`)); setEmployee(''); setReason('');
  });
  const startEdit = (row?: Row) => {
    setDraft(row ? { client_name: row.name, client_phone: row.phone || '', client_address: row.address || '', city: String(row.city_id || ''), service: String(row.service_id || ''), comment: row.comment || '', source: row.source || '', scheduled_at: inputDate(row.scheduled_at), appliance_type: row.appliance_type || '', brand: row.brand || '' } : blank);
    setEditing(true);
  };
  const saveLead = () => void run(async () => {
    const data = await request<Row>(selected ? `leads/${selected.id}/` : 'leads/', { ...draft, ...(selected ? { action: 'save' } : {}) });
    setSelected(data); setEditing(false); await load();
  });
  const act = (action: string) => void run(async () => {
    if (!selected?.kind) return;
    const result = await request<Row>(`${selected.kind === 'lead' ? 'leads' : 'orders'}/${selected.id}/`, { action, employee_id: employee ? Number(employee) : null, reason, expected_notice: selected.assignment_notice });
    setSelected(result); setReason(''); await load();
  });
  const confirm = (title: string, action: string) => Alert.alert(title, `Заказ № ${selected?.id}`, [{ text: 'Отмена', style: 'cancel' }, { text: 'Подтвердить', onPress: () => act(action) }]);
  const update = (key: keyof Draft) => (value: string) => setDraft(old => ({ ...old, [key]: value }));
  const tabs: Tab[] = manager ? ['dashboard', 'leads', 'orders', 'clients', 'team'] : ['leads', 'orders', 'clients'];
  const workers = options.workers.filter(w => w.available && selected?.service_id && w.service_ids.includes(selected.service_id) && selected.city_id && w.city_ids.includes(selected.city_id));

  return <SafeAreaView style={styles.safe}><KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <View style={styles.header}><View><Text style={styles.title}>CRM · {manager ? 'Руководитель' : 'Оператор'}</Text><Text style={styles.muted}>{session.name}</Text></View><Button title="Выйти" secondary disabled={busy} onPress={() => void run(onLogout)} /></View>
    <View><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs}>{tabs.map(value => <Button key={value} title={labels[value]} secondary={tab !== value} disabled={busy} onPress={() => { if (value === tab) { setSelected(null); setEditing(false); void run(() => load()); return; } setSelected(null); setEditing(false); setRows([]); setDashboard(null); setCount(0); setNext(false); setSearch(''); setQuery(''); setHistory(false); setTab(value); }} />)}</ScrollView></View>
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" refreshControl={<RefreshControl refreshing={busy} onRefresh={() => void refresh()} />}>
      {!!error && <View accessibilityRole="alert" style={styles.error}><Text style={ui.errorText}>{error}</Text></View>}
      {(selected || editing) && <Button title="← Назад" secondary disabled={busy} onPress={() => { if (editing) setEditing(false); else setSelected(null); }} />}
      {editing ? <View style={styles.card}><Text style={styles.heading}>{selected ? 'Редактирование лида' : 'Новый лид'}</Text>
        <Field label="Имя *" value={draft.client_name} set={update('client_name')} /><Field label="Телефон *" value={draft.client_phone} set={update('client_phone')} phone />
        <Choices title="Город *" items={options.cities} value={draft.city} onChange={update('city')} />
        <Field label="Адрес" value={draft.client_address} set={update('client_address')} />
        <Choices title="Услуга" items={options.services} value={draft.service} onChange={update('service')} />
        <Field label="Тип техники" value={draft.appliance_type} set={update('appliance_type')} /><Field label="Бренд" value={draft.brand} set={update('brand')} />
        <Field label="Комментарий" value={draft.comment} set={update('comment')} multiline />
        <Choices title="Источник" items={['OLX', 'Google', 'Instagram', 'Telegram', 'Телефон'].map((name, id) => ({ id, name }))} value={String(['OLX', 'Google', 'Instagram', 'Telegram', 'Телефон'].indexOf(draft.source))} onChange={value => update('source')(['OLX', 'Google', 'Instagram', 'Telegram', 'Телефон'][Number(value)])} />
        <Field label="Запись: ГГГГ-ММ-ДД ЧЧ:ММ (необязательно)" value={draft.scheduled_at} set={update('scheduled_at')} /><Text style={styles.muted}>Время CRM: Asia/Qyzylorda. Например: 2026-09-25 14:30.</Text>
        <Button title="Сохранить лид" disabled={busy || !draft.city || !draft.client_name.trim() || !draft.client_phone.trim()} onPress={saveLead} />
      </View> : selected ? <>
        <View style={styles.card}><Text style={styles.muted}>{selected.kind === 'lead' ? 'ЛИД' : 'ЗАКАЗ'} № {selected.id}</Text><Text style={styles.heading}>{selected.name}</Text><Text style={styles.badge}>{selected.status_label}</Text>
          <Text>{selected.service} · {selected.city}</Text><Text>{selected.phone}</Text><Text>{selected.address || 'Адрес не указан'}</Text>{!!selected.scheduled_at && <Text>Запись: {date(selected.scheduled_at)}</Text>}{!!selected.comment && <Text>{selected.comment}</Text>}{selected.kind === 'order' && <><Text>Мастер: {selected.employee}</Text>{selected.amount != null && <Text>Стоимость: {money(selected.amount)}</Text>}{!!selected.work_comment && <Text>Выполнено: {selected.work_comment}</Text>}</>}
        </View>
        {selected.kind === 'lead' && !['converted', 'lost', 'won'].includes(selected.status || '') && <View style={styles.card}><Button title="Редактировать" secondary disabled={busy} onPress={() => startEdit(selected)} /><Button title="Клиент согласился → создать заказ" disabled={busy} onPress={() => confirm('Создать заказ из лида?', 'convert')} /></View>}
        {selected.kind === 'order' && selected.status === 'new' && <View style={styles.card}><Choices title="Назначить мастера" items={workers} value={employee} onChange={setEmployee} /><Button title="Назначить выбранного мастера" disabled={busy || !employee} onPress={() => act('assign')} /><Button title="Автоматический подбор" secondary disabled={busy} onPress={() => { setEmployee(''); void run(async () => { const result = await request<Row>(`orders/${selected.id}/`, { action: 'assign' }); setSelected(result); await load(); }); }} /></View>}
        {selected.kind === 'order' && ['new', 'assigned', 'in_progress'].includes(selected.status || '') && <View style={styles.card}><Field label="Причина возврата / отмены" value={reason} set={setReason} multiline />{selected.assignment_notice && <Button title="Вернуть оператору" secondary disabled={busy || !reason.trim()} onPress={() => confirm('Вернуть заказ оператору?', 'return')} />}<Button title="Клиент отказался — отменить заказ" secondary disabled={busy || !reason.trim()} onPress={() => confirm('Отменить заказ?', 'cancel')} /></View>}
        <View style={styles.card}><Text style={styles.heading}>История</Text>{selected.events?.map(event => <View key={event.id} style={styles.event}><Text>{event.description}</Text><Text style={styles.muted}>{date(event.created_at)} · {event.actor_name}</Text></View>)}{!selected.events?.length && <Text style={styles.muted}>Событий пока нет</Text>}</View>
      </> : tab === 'dashboard' ? dashboard && <><Text style={styles.heading}>Сегодня · {dashboard.date}</Text><View style={styles.card}><Text style={styles.muted}>Выручка</Text><Text style={styles.total}>{money(dashboard.revenue)}</Text><Text>Оплаченных заказов: {dashboard.paid_count}</Text><Text>Расходы: {money(dashboard.expenses)}</Text></View><View style={styles.card}><Text>Активных лидов: {dashboard.new_leads}</Text><Text>Активных заказов: {dashboard.active_orders}</Text><Text>Без мастера: {dashboard.unassigned}</Text></View></> : <>
        <Text style={styles.heading}>{labels[tab]}</Text>
        {tab === 'leads' && <Button title="+ Новый лид" disabled={busy} onPress={() => startEdit()} />}
        {tab !== 'team' && <View style={styles.card}><Field label="Поиск по имени или телефону" value={search} set={setSearch} /><Button title="Найти" secondary disabled={busy} onPress={() => { if (query === search.trim()) void refresh(); else setQuery(search.trim()); }} />{['leads', 'orders'].includes(tab) && <View style={styles.wrap}><Button title="Активные" secondary={history} disabled={busy} onPress={() => setHistory(false)} /><Button title="История" secondary={!history} disabled={busy} onPress={() => setHistory(true)} /></View>}</View>}
        <Text style={styles.muted}>Найдено: {count}</Text>
        {rows.map(row => <View key={row.id} style={styles.card}><Text style={styles.heading}>{row.name}</Text>{row.kind && <Text>№ {row.id} · {row.status_label}</Text>}{!!row.service && <Text>{row.service} · {row.city}</Text>}{!!row.phone && <Text>{row.phone}</Text>}{!!row.address && <Text>{row.address}</Text>}{!!row.employee && <Text>Мастер: {row.employee}</Text>}{!!row.scheduled_at && <Text>Запись: {date(row.scheduled_at)}</Text>}{tab === 'clients' && <Text>Обращений: {row.lead_count} · Заказов: {row.order_count}</Text>}{row.kind && <Button title="Открыть" secondary disabled={busy} onPress={() => open(row)} />}
          {tab === 'team' && <><Text>{row.active ? 'Активен' : 'Отключён'} · Заказов в работе: {row.active_orders}</Text><Text>Города: {row.cities?.join(', ') || 'Не указаны'}</Text>{row.rates?.map((rate, index) => <Text key={index}>{rate.service}: {rate.percentage}% {rate.active ? '' : '(ставка выключена)'}</Text>)}<Button title={row.available ? 'Приём включён — выключить' : 'Приём выключен — включить'} secondary disabled={busy || !row.active} onPress={() => void run(async () => { await request(`team/${row.id}/availability/`, { available: !row.available }); await load(); })} /></>}
        </View>)}
        {!rows.length && !busy && <Text style={styles.muted}>Записей пока нет</Text>}{next && <Button title="Показать ещё" secondary disabled={busy} onPress={() => void run(() => load(page + 1))} />}
      </>}
    </ScrollView>
  </KeyboardAvoidingView></SafeAreaView>;
}

const styles = StyleSheet.create({
  safe: ui.safe, header: { padding: 16, backgroundColor: '#fff', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }, title: { fontSize: 18, fontWeight: '700', color: '#234b3d' }, heading: ui.heading, muted: ui.muted, total: { fontSize: 30, fontWeight: '700', color: '#176b55' },
  tabs: { gap: 7, padding: 12 }, content: { padding: 16, gap: 14, paddingBottom: 40 }, card: ui.card, field: ui.field, label: ui.label, wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 }, chip: { padding: 10, borderWidth: 1, borderColor: '#cddbd1', borderRadius: 8, backgroundColor: '#fff' }, selected: { backgroundColor: '#176b55', borderColor: '#176b55' }, selectedText: { color: '#fff', fontSize: 13 }, badge: { color: '#176b55', fontWeight: '600' }, error: ui.error, event: { paddingVertical: 8, borderTopWidth: 1, borderTopColor: '#e9eeea', gap: 5 },
});
