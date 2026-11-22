// Response shapes of the MarketLens API (mirrors backend/app/services/*).

export type Num = number | null;

export interface Quote {
  symbol: string;
  price: Num;
  prev_close: Num;
  change: Num;
  change_pct: Num;
  open: Num;
  high: Num;
  low: Num;
  volume: Num;
  currency: string | null;
  name: string | null;
  exchange: string | null;
  market_state: string | null;
  quote_time: string | null;
  delay_minutes: number | null;
  realtime: boolean;
  source: string;
  pre_market_price: Num;
  post_market_price: Num;
  fifty_two_week_high: Num;
  fifty_two_week_low: Num;
}

export interface SessionStatus {
  market: string;
  state: "open" | "pre" | "post" | "closed";
  local_time: string;
  next_open: string | null;
  session_close: string | null;
}

export interface SnapshotCard extends Partial<Quote> {
  symbol: string;
  name: string;
  region: string;
  trend: { label: string; vs_sma20_pct?: number; vs_sma50_pct?: number; mom20_pct?: number };
  sparkline: number[];
  status: string | null;
}

export interface Snapshot {
  cards: SnapshotCard[];
  sessions: { US: SessionStatus; KR: SessionStatus };
  updated_at: string;
}

export interface Reason {
  text: string;
  category: string;
  points: number;
  direction: "supportive" | "headwind";
}

export interface Regime {
  label: "Risk-On" | "Neutral" | "Risk-Off";
  score: number;
  quadrant: string;
  quadrant_probabilities: Record<string, number>;
  overlays: { name: string; reason: string }[];
  subscores: Record<string, number>;
  reasons: Reason[];
  rules: { category: string; name: string; score: number; weight: number; text: string; value: number | null }[];
  formula: string;
  notes: string[];
  factor_trends: Record<string, { value: number; text: string }>;
  data_as_of: Record<string, string>;
  updated_at: string;
  fred_mode: string;
}

export interface MacroCard {
  key: string;
  label: string;
  group: string;
  status?: string;
  unit?: string;
  decimals?: number;
  frequency?: string;
  value?: number;
  date?: string;
  previous?: Num;
  previous_label?: string;
  previous2?: Num;
  previous2_label?: string;
  change?: Num;
  direction?: "up" | "down" | "flat" | null;
  avg_5y?: Num;
  percentile_5y?: Num;
  zscore_5y?: Num;
  equity_effect?: "positive" | "negative" | "mixed" | "neutral";
  impact?: string;
  source?: string;
  series_id?: string;
  sparkline?: number[];
}

export interface ChainNode {
  node: string;
  type: "macro" | "asset" | "sector" | "stock";
  direction: "up" | "down" | "mixed";
}

export interface NewsItem {
  id: number;
  title: string;
  url: string | null;
  source: string | null;
  provider: string | null;
  published_at: string | null;
  language: string | null;
  scope: string | null;
  tickers: string[];
  cluster_size: number;
  also: { title: string; source: string | null; url: string | null; published_at: string | null }[];
  priority: number;
  category: string;
  event_type: string | null;
  sentiment: "Very Bullish" | "Bullish" | "Neutral" | "Bearish" | "Very Bearish";
  impact: "Low" | "Medium" | "High" | "Critical";
  importance: number;
  confidence: number;
  sectors: string[];
  chain: ChainNode[];
  rationale: string | null;
  method: "ai" | "rules";
  model: string | null;
}

export interface NewsSummary {
  headline: string;
  bullets: string[];
  risks: string[];
  opportunities: string[];
  themes: { theme: string; direction: string; evidence_ids: string[] }[];
  method: "ai" | "rules";
  model: string | null;
  generated_at: string;
  data_from: string | null;
  data_to: string | null;
  input_count: number;
}

export interface NewsResponse {
  status?: string;
  items: NewsItem[];
  filings?: NewsItem[];
  summary: NewsSummary | null;
  method?: { method: string; model: string | null; ai_enabled: boolean; ai_error: string | null };
  categories?: Record<string, number>;
  raw_count?: number;
  unique_count?: number;
  fetched_at: string;
  sources?: string[];
}

export interface Component {
  key: string;
  label: string;
  score: number;
  details: string[];
  weight?: number;
  points?: number;
}

export interface Horizon {
  score: number;
  view: "Bullish" | "Neutral" | "Bearish";
  band: string;
  range: string;
  components: Component[];
}

export interface Level {
  label: string;
  price: number;
  strength: number;
  distance_pct: number;
  sources: string[];
  projected: boolean;
}

export interface PatternEvent {
  pattern: string;
  direction: "bullish" | "bearish" | "neutral";
  status: "confirmed" | "forming";
  idx: number;
  date: string;
  start_date: string;
  confidence: number;
  levels: Record<string, number>;
  description: string;
  bars_ago?: number;
  historical?: { n: number; success_rate: number | null; avg_signed_return: number; median_signed_return: number; horizon: number; sufficient: boolean } | null;
}

export interface VolumeProfile {
  bins: { price_low: number; price_high: number; price: number; volume: number; in_value_area: boolean }[];
  poc: number;
  vah: number;
  val: number;
  start: string;
  end: string;
}

export interface SignalResult {
  score: number;
  signal: "STRONG BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG SELL";
  band: string;
  components: Component[];
  weights: Record<string, number>;
  thresholds: Record<string, number>;
  regime: string | null;
  regime_note: string | null;
  gates: string[];
  bullish_factors: string[];
  bearish_factors: string[];
  horizons: { short: Horizon; medium: Horizon; long: Horizon };
  formula: string;
  previous?: { as_of: string; score: number; signal: string; components: Record<string, number> };
}

export interface Technical {
  symbol: string;
  as_of: string;
  signal: SignalResult;
  ma: {
    structure: string;
    order: string;
    events: { event: string; direction: string; date: string; bars_ago: number; kind?: "golden" | "death" | "price200" }[];
    states: { state: string; bullish: boolean; detail: string }[];
    spread_regime: string;
    ma_spread_pct: Num;
    slopes_pct: Record<string, number>;
    smas: Record<string, Num>;
  };
  rsi: { value: Num; zone: string; momentum_5d: Num; slope_14d: Num; trend: string | null; divergences: { type: string; direction: string; date: string; detail: string }[]; context: string };
  support_resistance: { supports: Level[]; resistances: Level[]; nearest_support_pct: number; nearest_resistance_pct: number; reward_risk: Num; atr: number };
  volume_profile: VolumeProfile | null;
  volume_profile_period: string;
  patterns: PatternEvent[];
  indicators: Record<string, Num>;
  rsi_periods?: RsiPeriods;
  regime: string | null;
  data: { source: string; fetched_at: string; quality: { ok: boolean; issues: { check: string; severity: string; count?: number; detail?: string; sample?: string[] }[] }; last_date: string };
}

export interface Bar {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: Num;
  [k: string]: number | string | null | undefined;
}

export interface ChartData {
  symbol: string;
  range: string;
  interval: string;
  intraday: boolean;
  source: string;
  bars: Bar[];
  /** First bar of the selected range; earlier bars are extra history for zooming out. */
  visible_from?: string | null;
  fetched_at?: string;
  rsi_periods?: RsiPeriods;
  stoch_sets?: Record<"short" | "medium" | "long", number[]>;
  /** Trendlines price is respecting now, most credible first. */
  trendlines?: Trendline[];
  trend_pattern?: { name: string; bias: string; support: string; resistance: string; width_pct: number; touches: number; note: string } | null;
  /** How far the last price is below the highs of the selected range / 52 weeks / all loaded history. */
  drawdown?: { last: number; basis: string; peaks: Peak[] } | null;
}

export interface Peak {
  key: string; keys: string[]; label: string; peak: number; date: string; time: string | number; pct: number | null; days: number;
  trough: number; trough_pct: number | null; rebound_pct: number | null;
}

export interface Trendline {
  kind: "support" | "resistance"; name: string; confirmed: boolean; touches: number; violations: number; span_bars: number;
  score: number; start: { time: string | number; value: number }; end: { time: string | number; value: number };
  touch_points: { time: string | number; value: number }[]; slope_pct_per_20: number; distance_pct: number;
  last_touch_bars_ago: number; note: string;
}

export interface RsiPeriods { short: number; medium: number; long: number }

export interface Profile {
  symbol: string;
  name: string;
  name_ko?: string | null;
  exchange: string | null;
  currency: string | null;
  sector: string | null;
  industry: string | null;
  country?: string | null;
  summary?: string | null;
  earnings_timestamp?: number | null;
  fundamentals: Record<string, Num | string>;
}

export interface Overview {
  symbol: string;
  quote: Quote;
  profile: Profile;
  market_status: SessionStatus;
  updated_at: string;
}

export interface Scores {
  symbol: string;
  overall: Num;
  overall_band: string | null;
  rating: string | null;
  technical: number;
  technical_signal: string;
  macro: Num;
  fundamental: Num;
  blend: Record<string, number>;
  weights_used: Record<string, number>;
  macro_detail: {
    score: Num;
    reasons: string[];
    risks: string[];
    formula?: string;
    empirical?: { score: number; tailwind_pct: number; r2: number; market_beta: number; details: { factor: string; move: string; sensitivity: string; effect_pct: number }[] } | null;
    group?: { group: string; score: number; contributions: { factor: string; move: string; points: number; evidence: string }[] } | null;
    regime?: { label: string; score: number; quadrant: string };
  };
  fundamental_detail: { score: Num; band: string | null; pillars: Record<string, { score: Num; items: { field: string; label: string; value: number; score: number }[] }>; missing: string[] };
  horizons: { short: Horizon; medium: Horizon; long: Horizon };
  change: { from_date: string; to_date: string; technical: [Num, Num]; macro: [Num, Num]; signal: [string | null, string | null]; drivers: { component: string; from: number; to: number }[] } | null;
  as_of: string;
  disclaimer: string;
}

export interface AnalogHorizon {
  horizon: number;
  n: number;
  positive: number;
  negative: number;
  win_rate: Num;
  win_rate_ci95: [number, number] | null;
  median_return: Num;
  mean_return: Num;
  p25: Num;
  p75: Num;
  worst: Num;
  best: Num;
  base_win_rate: Num;
  base_median_return: Num;
  sufficient: boolean;
}

export interface Analogs {
  status: string;
  message: string | null;
  as_of: string;
  lookback_years: number;
  window_start: string;
  n_matches: number;
  matches: { date: string; distance: number; features: Record<string, number>; forward: Record<string, Num> }[];
  horizons: Record<string, AnalogHorizon>;
  current_features: Record<string, { label: string; value: number }>;
  method: { max_distance: number; min_separation_days: number; embargo_days: number; min_sample: number; weights: Record<string, number> };
  disclaimer: string;
  benchmark: string;
}

export interface ScanRow {
  pbr?: number | null; roe?: number | null; fin_score?: number | null;
  symbol: string;
  name: string;
  name_ko?: string | null;
  market: string;
  group: string | null;
  price: number;
  change_pct: Num;
  score: Num;
  technical: number;
  macro: Num;
  signal: string;
  trend: string | null;
  rsi: Num;
  mom20: Num;
  vol_ratio: Num;
  pattern: string | null;
  volatility: Num;
  risk: string;
  reason: string[];
  risks: string[];
  macro_reasons?: string[];
  time_horizon: string;
  horizons?: Record<string, number>;
  combined?: number;
  rating?: string;
  overlap?: boolean;
  breakdown?: { technical: number; macro: number; w_tech: number; w_macro: number };
  market_cap?: Num;
  pe?: Num;
  forward_pe?: Num;
  revenue_growth?: Num;
  eps_growth?: Num;
  overall?: Num;
  dist_sma200?: Num;
  as_of?: string;
}

export interface ScanMeta {
  scan: { state: string; started_at: string | null; finished_at: string | null; processed: number; total: number; errors: number; message: string | null };
  results: number;
  note: string | null;
  /** Medium + long slow stochastics both ≥ 80: left out of every ranking (user's rule). */
  excluded_overbought?: { count: number; symbols: string[]; rule: string; pending?: number;
    groups?: { key: string; label: string; rule: string; count: number; items: { symbol: string; name?: string | null; why?: string[] | null }[] }[] } | null;
}

export interface Settings {
  profile: { risk_tolerance: string; holding_period: string; preferred_market: string; preferred_sectors: string[]; max_drawdown: number; base_currency: string };
  weights: { technical_components: Record<string, number>; overall_blend: Record<string, number>; combined_ranking: Record<string, number> };
  preferences: { color_convention: "global" | "korea"; rsi_period: number; rsi_short_period: number; rsi_long_period: number;
    lower_osc?: "stoch" | "rsi"; stoch_sets?: Record<"short" | "medium" | "long", number[]>; default_range: string; indicators: Record<string, boolean>; volume_profile_period: string;
    timing_style?: "user" | "evidence" };
  last_visit_at: string | null;
  previous_visit_at: string | null;
  reference: { signal_thresholds: Record<string, number>; score_bands: [number, string][]; default_weights: Settings["weights"] };
}
