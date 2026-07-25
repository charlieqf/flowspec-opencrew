import type { UsdCnyRateState } from "./types";

export type PriceKind = "image" | "video" | "lipsync";

export type MediaPricePoint = {
  kind: PriceKind;
  provider: string;
  providerLabel: string;
  model: string;
  variant: string;
  amount: number;
  currency: "USD" | "CNY";
  unit: string;
};

export const FALLBACK_USD_CNY_RATE = 7.1;

export const MEDIA_PRICE_POINTS: MediaPricePoint[] = [];

export const VIDEO_MIN_DURATION_SECONDS: Record<string, number> = {};

export function formatCurrencyAmount(amount: number, currency: "USD" | "CNY") {
  const prefix = currency === "USD" ? "$" : "¥";
  if (amount >= 100) return `${prefix}${amount.toFixed(0)}`;
  if (amount >= 1) return `${prefix}${amount.toFixed(2).replace(/\.00$/, "")}`;
  return `${prefix}${amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

export function unitLabel(unit: string) {
  if (unit === "second") return "秒";
  if (unit === "image") return "张";
  if (unit === "bean") return "豆";
  return unit;
}

export async function loadUsdCnyRate(current: UsdCnyRateState): Promise<UsdCnyRateState> {
  const today = new Date().toISOString().slice(0, 10);
  if (current.date === today && !current.error) return current;
  try {
    const res = await fetch("https://open.er-api.com/v6/latest/USD");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json() as { rates?: { CNY?: number } };
    const rate = Number(payload.rates?.CNY);
    if (!Number.isFinite(rate) || rate <= 0) throw new Error("USD/CNY rate missing");
    return { rate, date: today, source: "open.er-api.com", loading: false, error: "" };
  } catch (err) {
    return { rate: FALLBACK_USD_CNY_RATE, date: today, source: "fallback", loading: false, error: err instanceof Error ? err.message : "Failed loading exchange rate" };
  }
}
