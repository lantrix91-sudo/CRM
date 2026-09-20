import { StyleSheet } from 'react-native';

export const colors = {
  primary: '#176b55', text: '#253f36', muted: '#697970',
  background: '#f3f6f4', surface: '#ffffff', border: '#dce5df',
  secondary: '#e9f1eb', input: '#fafcf9', danger: '#923d34', error: '#ffebe8',
} as const;
export const ui = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  card: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 12, padding: 16, gap: 10 },
  heading: { fontSize: 18, fontWeight: '600', color: colors.text },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  error: { backgroundColor: colors.error, padding: 14, borderRadius: 10 },
  errorText: { color: colors.danger },
  field: { gap: 6, marginBottom: 5 },
  label: { fontSize: 13, color: colors.text },
  input: { padding: 12, minHeight: 44, borderRadius: 8, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.input, color: colors.text, fontSize: 16 },
  multiline: { minHeight: 88, textAlignVertical: 'top' },
  button: { backgroundColor: colors.primary, paddingHorizontal: 14, paddingVertical: 12, minHeight: 44, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  secondary: { backgroundColor: colors.secondary, borderColor: colors.border, borderWidth: 1 },
  buttonText: { color: colors.surface, fontSize: 14, fontWeight: '600', textAlign: 'center' },
  secondaryText: { color: colors.primary },
  disabled: { opacity: .45 },
});
