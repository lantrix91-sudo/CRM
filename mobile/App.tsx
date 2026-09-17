import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, AppState, BackHandler, Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View, KeyboardAvoidingView, Platform } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as SecureStore from 'expo-secure-store';
import { api, ApiError, serverURL, Session, Order, Page, Preview, Profile, Summary } from './src/api';

const KEY = 'crm-worker-session';
const money = (value: string | null | undefined) => value == null ? 'Не рассчитано' : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ₸';
function Button({ title, onPress, disabled = false, secondary = false }: { title: string; onPress: () => void; disabled?: boolean; secondary?: boolean }) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[styles.button, secondary && styles.secondary, disabled && { opacity: .45 }]}>
    <Text style={[styles.buttonText, secondary && { color: '#176451' }]}>{title}</Text></Pressable>;
}
function Field({ label, value, set, secure = false, numeric = false, multiline = false }: {
  label: string; value: string; set: (s: string) => void; secure?: boolean; numeric?: boolean; multiline?: boolean;
}) {
  return <View><Text style={styles.label}>{label}</Text><TextInput accessibilityLabel={label} style={[styles.input, multiline && { minHeight: 88 }]}
    value={value} onChangeText={set} secureTextEntry={secure} keyboardType={numeric ? 'decimal-pad' : 'default'}
    autoCapitalize="none" autoCorrect={false} multiline={multiline} /></View>;
}
function Finance({ title, data }: { title: string; data: Summary }) {
  return <View style={styles.card}><Text style={styles.heading}>{title}</Text><Text style={styles.muted}>{data.order_count} оплаченных заказов</Text>
    <Text style={styles.total}>{money(data.worker_amount)}</Text><Text>Доля мастера</Text>
    <Text style={styles.line}>Стоимость услуг: {money(data.revenue)}</Text><Text style={styles.line}>Расходы: {money(data.expenses)}</Text>
    {data.balance_due !== undefined && <Text style={styles.line}>К передаче компании: {money(data.balance_due)}</Text>}
  </View>;
}
export default function App() {
  const [ready, setReady] = useState(false), [session, setSession] = useState<Session | null>(null);
  const [server, setServer] = useState(''), [username, setUsername] = useState(''), [password, setPassword] = useState('');
  const [tab, setTab] = useState<'active' | 'history' | 'profile'>('active');
  const [orders, setOrders] = useState<Order[]>([]), [page, setPage] = useState(1), [hasNext, setHasNext] = useState(false);
  const [selected, setSelected] = useState<Order | null>(null), [profile, setProfile] = useState<Profile | null>(null);
  const [amount, setAmount] = useState(''), [expenses, setExpenses] = useState('0'), [comment, setComment] = useState('');
  const [method, setMethod] = useState('cash'), [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const running = useRef(false);
  async function perform(task: () => Promise<void>) {
    if (running.current) return;
    running.current = true; setBusy(true); setError('');
    try { await task(); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401) { await SecureStore.deleteItemAsync(KEY); setSession(null); setSelected(null); setOrders([]); setProfile(null); }
      setError(e instanceof Error ? e.message : 'Не удалось выполнить действие');
    } finally { running.current = false; setBusy(false); }
  }
  useEffect(() => {
    SecureStore.getItemAsync(KEY).then(value => {
      if (value) { const saved = JSON.parse(value) as Session; serverURL(saved.server); setSession(saved); setServer(saved.server); }
    }).catch(() => setError('Не удалось восстановить вход. Войдите заново.')).finally(() => setReady(true));
  }, []);
  async function refresh(nextPage = 1) {
    if (!session) return;
    if (selected) {
      const value = await api<Order>(session.server, 'orders/' + selected.id + '/', session.token);
      setSelected(value); setPreview(null);
    } else if (tab === 'profile') setProfile(await api<Profile>(session.server, 'profile/', session.token));
    else {
      const data = await api<Page>(session.server, 'orders/?scope=' + tab + '&page=' + nextPage, session.token);
      setOrders(old => nextPage === 1 ? data.results : [...old, ...data.results]); setPage(nextPage); setHasNext(Boolean(data.next));
    }
  }
  useEffect(() => { if (session && !selected) void perform(() => refresh()); }, [session, tab, selected?.id]);
  useEffect(() => {
    const sub = AppState.addEventListener('change', state => { if (state === 'active' && session) void perform(() => refresh()); });
    return () => sub.remove();
  }, [session, selected, tab]);
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (preview) { setPreview(null); return true; }
      if (selected) { setSelected(null); return true; }
      return false;
    }); return () => sub.remove();
  }, [selected, preview]);
  async function login() {
    const origin = serverURL(server);
    const result = await api<{ token: string; name: string }>(origin, 'login/', undefined, { username, password });
    const value = { ...result, server: origin };
    await SecureStore.setItemAsync(KEY, JSON.stringify(value)); setPassword(''); setSession(value);
  }
  async function openOrder(order: Order) {
    if (!session) return;
    setSelected(await api<Order>(session.server, 'orders/' + order.id + '/', session.token));
    setAmount(order.repeat_of ? '0' : ''); setExpenses('0'); setComment(order.repeat_of ? 'Повторный ремонт выполнен бесплатно' : ''); setPreview(null);
  }
  async function action(name: string) {
    if (!session || !selected) return;
    const data = await api<Order | Preview | { rejected: boolean }>(session.server, 'orders/' + selected.id + '/action/', session.token, {
      action: name, amount: selected.repeat_of ? '0' : amount.replace(',', '.'),
      expenses: selected.repeat_of ? '0' : expenses.replace(',', '.'), comment,
      confirmation: preview?.confirmation, received_amount: amount.replace(',', '.'), received_method: method,
    });
    if (name === 'preview') setPreview(data as Preview);
    else if ('rejected' in data) { setSelected(null); setOrders(old => old.filter(o => o.id !== selected.id)); }
    else { setSelected(data as Order); setPreview(null); }
  }
  const runAction = (name: string) => void perform(() => action(name));
  function confirm(title: string, name: string) {
    Alert.alert(title, 'Подтвердите действие по заказу № ' + selected?.id, [{ text: 'Отмена', style: 'cancel' }, { text: 'Подтвердить', onPress: () => runAction(name) }]);
  }
  function changeTab(value: typeof tab) { if (!busy && value === tab && !selected) { void perform(() => refresh()); return; } if (!busy) { setTab(value); setSelected(null); setPreview(null); setOrders([]); setError(''); } }
  const link = (url: string) => void perform(async () => { await Linking.openURL(url); });
  return <SafeAreaProvider><SafeAreaView style={styles.safe}><StatusBar style="dark" />
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <View style={styles.header}><Text style={styles.logo}>CRM · Мастер</Text><Text style={styles.muted}>{session?.name || 'Работа под рукой'}</Text></View>
    {!ready ? <ActivityIndicator /> : <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
      refreshControl={session ? <RefreshControl refreshing={busy} onRefresh={() => void perform(() => refresh())} /> : undefined}>
      {!!error && <View accessibilityRole="alert" style={styles.error}><Text style={{ color: '#9d2424' }}>{error}</Text></View>}
      {!session ? <View style={styles.card}><Text style={styles.heading}>Вход для мастера</Text><Text style={styles.muted}>Используйте учётную запись вашей CRM.</Text>
        <Field label={__DEV__ ? "Адрес сервера (HTTPS или локальный HTTP)" : "Адрес сервера (https://…)"} value={server} set={setServer} />
        <Field label="Логин" value={username} set={setUsername} /><Field label="Пароль" value={password} set={setPassword} secure />
        <Button title={busy ? 'Входим…' : 'Войти'} disabled={busy || !server || !username || !password} onPress={() => void perform(login)} />
      </View> : selected ? <>
        <Button title="← К списку" secondary disabled={busy} onPress={() => { setSelected(null); setPreview(null); }} />
        <View style={styles.card}><Text style={styles.muted}>ЗАКАЗ № {selected.id}</Text><Text style={styles.heading}>{selected.service}</Text>
          <Text style={styles.badge}>{selected.status_label}</Text>{!!selected.repeat_of && <Text>Повторный ремонт заказа № {selected.repeat_of}</Text>}
          {!!selected.client && <Text style={styles.line}>{selected.client}</Text>}<Text style={styles.line}>{selected.address || 'Адрес не указан'}</Text>
          {!!selected.appliance_type && <Text>{selected.appliance_type} · {selected.brand}</Text>}{!!selected.comment && <Text style={styles.line}>{selected.comment}</Text>}
          {selected.phone ? <><Button title={'Позвонить: ' + selected.phone} secondary onPress={() => link('tel:' + selected.phone!.replace(/[^+0-9]/g, ''))} />
            <Button title="Написать в WhatsApp" secondary onPress={() => link('https://wa.me/' + selected.phone!.replace(/\D/g, ''))} /></> : <Text style={styles.muted}>Телефон доступен после принятия заказа.</Text>}
        </View>
        {selected.actions.includes('start') && <Button title="Принять и начать" disabled={busy} onPress={() => runAction('start')} />}
        {selected.actions.includes('reject') && <Button title="Отклонить заказ" secondary disabled={busy} onPress={() => confirm('Вернуть заказ оператору?', 'reject')} />}
        {selected.actions.includes('complete') && <View style={styles.card}><Text style={styles.heading}>Завершение работы</Text>
          {preview ? <><Text>Получено: {money(preview.amount)}</Text><Text>Расходы: {money(preview.expenses)}</Text><Text>Моя доля: {money(preview.worker_share)}</Text>
            <Text style={styles.line}>{preview.comment}</Text>{Number(preview.amount) === 0 && <Text style={styles.badge}>Бесплатно · оплата не требуется</Text>}
            <Button title="Подтвердить завершение" disabled={busy} onPress={() => runAction('complete')} /><Button title="Исправить" secondary disabled={busy} onPress={() => setPreview(null)} />
          </> : <>{!selected.repeat_of && <><Field label="Получено, KZT (0 — бесплатно)" value={amount} set={setAmount} numeric /><Field label="Расходы, KZT" value={expenses} set={setExpenses} numeric /></>}
            <Field label="Что сделано" value={comment} set={setComment} multiline /><Button title="Проверить расчёт" disabled={busy} onPress={() => runAction('preview')} /></>}
        </View>}
        {selected.actions.includes('payment') && <View style={styles.card}><Text style={styles.heading}>Получение оплаты</Text><Field label="Полученная сумма, KZT" value={amount} set={setAmount} numeric />
          {(['cash', 'transfer', 'card'] as const).map((v, i) => <Button key={v} secondary={method !== v} title={['Наличные', 'Перевод', 'Карта'][i]} onPress={() => setMethod(v)} />)}
          <Button title="Получил оплату" disabled={busy} onPress={() => confirm('Сохранить оплату?', 'payment')} /></View>}
        {!selected.actions.length && <View style={styles.card}><Text style={styles.heading}>{selected.is_free ? 'Оплата не требуется' : selected.status_label}</Text>
          {selected.amount !== null && <Text>Сумма: {money(selected.amount)}</Text>}<Text>{selected.work_comment}</Text></View>}
        <View style={styles.card}><Text style={styles.heading}>История заказа</Text>{selected.events?.map(e => <View key={e.id} style={styles.event}>
          <Text style={styles.muted}>{new Date(e.created_at).toLocaleString('ru-RU')} · {e.actor_name}</Text><Text>{e.description}</Text></View>)}</View>
      </> : tab === 'profile' ? <>
        <Text style={styles.title}>Мои расчёты</Text>{profile && <><Finance title="Сегодня" data={profile.dashboard.today} />
          <Finance title="Текущая смена" data={profile.dashboard.current_shift} /><Finance title="Этот месяц" data={profile.dashboard.month} /></>}
        <Button title="Выйти" secondary disabled={busy} onPress={() => void perform(async () => {
          await api(session.server, 'logout/', session.token, {}); await SecureStore.deleteItemAsync(KEY); setSession(null); setOrders([]); setProfile(null);
        })} />
      </> : <><Text style={styles.title}>{tab === 'active' ? 'Мои заказы' : 'История заказов'}</Text>
        <Text style={styles.muted}>Потяните вниз, чтобы обновить</Text>
        {!orders.length && !busy && <View style={styles.card}><Text>Заказов пока нет</Text></View>}
        {orders.map(o => <Pressable accessibilityRole="button" key={o.id} disabled={busy} style={styles.card} onPress={() => void perform(() => openOrder(o))}>
          <Text style={styles.muted}>№ {o.id}{o.repeat_of ? ' · Повторный ремонт' : ''}</Text><Text style={styles.heading}>{o.service}</Text>
          <Text style={styles.badge}>{o.status_label}</Text><Text style={styles.line}>{o.address || 'Адрес не указан'}</Text><Text style={styles.link}>Открыть заказ →</Text>
        </Pressable>)}
        {hasNext && <Button title="Показать ещё" disabled={busy} onPress={() => void perform(() => refresh(page + 1))} />}
      </>}
      {busy && <ActivityIndicator color="#176451" />}
    </ScrollView>}
    {session && <View style={styles.tabs}>{(['active', 'history', 'profile'] as const).map((t, i) =>
      <Pressable key={t} disabled={busy} accessibilityRole="tab" accessibilityState={{ selected: tab === t }} onPress={() => changeTab(t)} style={styles.tab}>
        <Text style={{ color: tab === t ? '#176451' : '#70817c', fontWeight: tab === t ? '700' : '400' }}>{['Заказы', 'История', 'Расчёты'][i]}</Text></Pressable>)}</View>}
    </KeyboardAvoidingView></SafeAreaView></SafeAreaProvider>;
}
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#f3f7f5' }, header: { padding: 20, backgroundColor: '#fff', borderBottomWidth: 1, borderColor: '#e0e9e4' },
  logo: { fontSize: 23, fontWeight: '800', color: '#176451' }, content: { padding: 18, gap: 14, paddingBottom: 32 },
  card: { backgroundColor: '#fff', padding: 20, borderRadius: 18, borderWidth: 1, borderColor: '#e0e9e4', gap: 8 },
  heading: { fontSize: 20, fontWeight: '700', color: '#183c31', marginBottom: 6 }, title: { fontSize: 28, fontWeight: '800', color: '#183c31' },
  muted: { color: '#697e75', fontSize: 13 }, label: { color: '#365649', marginBottom: 7, marginTop: 10 },
  input: { padding: 14, borderRadius: 10, borderWidth: 1, borderColor: '#c7d7ce', fontSize: 16, backgroundColor: '#fafcfb', color: '#183c31' },
  button: { backgroundColor: '#176451', padding: 15, borderRadius: 11, alignItems: 'center', marginTop: 7, minHeight: 48 },
  secondary: { backgroundColor: '#e6f2eb' }, buttonText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  badge: { color: '#176451', backgroundColor: '#e6f2eb', padding: 9, borderRadius: 7, alignSelf: 'flex-start', fontWeight: '600' },
  line: { marginTop: 9, color: '#314d41', lineHeight: 22 }, link: { color: '#176451', marginTop: 8, fontWeight: '600' },
  total: { fontSize: 30, fontWeight: '800', color: '#176451' }, event: { paddingVertical: 10, borderBottomWidth: 1, borderColor: '#edf2ef', gap: 5 },
  tabs: { flexDirection: 'row', backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e0e9e4' }, tab: { flex: 1, alignItems: 'center', paddingVertical: 20 },
  error: { backgroundColor: '#ffebea', padding: 15, borderRadius: 12 },
});
