import React from 'react';
import { Text, TextInput, View, type TextInputProps } from 'react-native';
import { ui } from '../theme';

type Props = {
  label: string; value: string; set: (value: string) => void;
  secure?: boolean; numeric?: boolean; phone?: boolean; multiline?: boolean;
  autoCapitalize?: TextInputProps['autoCapitalize']; autoCorrect?: boolean;
};
export function Field({ label, value, set, secure = false, numeric = false, phone = false,
  multiline = false, autoCapitalize = 'sentences', autoCorrect = true }: Props) {
  return <View style={ui.field}><Text style={ui.label}>{label}</Text>
    <TextInput accessibilityLabel={label} style={[ui.input, multiline && ui.multiline]} value={value}
      onChangeText={set} secureTextEntry={secure} keyboardType={phone ? 'phone-pad' : numeric ? 'decimal-pad' : 'default'}
      autoCapitalize={secure ? 'none' : autoCapitalize} autoCorrect={secure ? false : autoCorrect} multiline={multiline} />
  </View>;
}
