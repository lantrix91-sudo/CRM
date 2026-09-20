import React from 'react';
import { Pressable, Text } from 'react-native';
import { ui } from '../theme';

type Props = { title: string; onPress: () => void; disabled?: boolean; secondary?: boolean };
export function Button({ title, onPress, disabled = false, secondary = false }: Props) {
  return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress}
    style={[ui.button, secondary && ui.secondary, disabled && ui.disabled]}>
    <Text style={[ui.buttonText, secondary && ui.secondaryText]}>{title}</Text>
  </Pressable>;
}
