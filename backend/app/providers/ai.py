"""AI analysis provider (Anthropic Claude API).

Roles are split into separate, schema-constrained calls:
  1. news classification + sentiment + impact + News->Macro->Sector->Stock chain
  2. market / stock narrative summary (grounded in the classified headlines + macro numbers)

Every call returns JSON validated by `output_config.format` (json_schema). The
static system prompt is marked for prompt caching. If no key is configured, or a
call fails, callers fall back to the transparent rule-based classifier and the
UI labels the result "rules" instead of "AI".
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import get_settings
from .base import health

log = logging.getLogger(__name__)

SENTIMENTS = ["Very Bullish", "Bullish", "Neutral", "Bearish", "Very Bearish"]
IMPACTS = ["Low", "Medium", "High", "Critical"]
CATEGORIES = ["Fed", "Inflation", "Treasury", "Geopolitics", "Oil", "AI", "Semiconductors", "China", "Japan",
              "Korea", "Europe", "Economic Data", "Earnings", "Guidance", "Analyst Action", "Product",
              "Partnership", "M&A", "Regulation/Legal", "SEC Filing", "Corporate", "Crypto", "Other"]

SYSTEM_PROMPT = """You are the news-analysis engine of an equity research terminal covering US and Korean markets.
You receive recent headlines (title, source, publish time, optional summary and related tickers) and return a
structured assessment for each one. The output feeds ranking and explanations shown to investors, so be
calibrated and concrete. Never invent facts that are not supported by the headline/summary; when the headline is
ambiguous, lower the confidence instead of guessing.

For each item decide:
- category: the single best topic from the allowed list.
- event_type: short snake_case label such as earnings_beat, earnings_miss, guidance_raise, guidance_cut,
  analyst_upgrade, analyst_downgrade, price_target_change, product_launch, partnership, acquisition, lawsuit,
  regulatory_action, macro_release, central_bank_signal, geopolitical_event, commodity_move, market_move,
  filing, opinion, other.
- sentiment: the likely direction of the price effect on the most directly affected assets (not the tone of the
  writing). Opinion pieces and listicles are usually Neutral with low confidence.
- impact: Low (minor/opinion/rehash), Medium (company- or sector-relevant), High (moves a large-cap stock, a
  sector, or rates/FX), Critical (systemic: surprise central-bank action, major geopolitical escalation, crisis).
- importance: integer 0-100 combining impact, surprise, breadth of affected assets, and source credibility.
- confidence: 0-1 in your sentiment/impact call.
- tickers: affected listed tickers (Yahoo format, e.g. NVDA, 005930.KS) - only when clearly implied.
- sectors: affected GICS-style sectors or industries (e.g. Semiconductors, Software, Banks, Energy).
- chain: the transmission path from the news to markets as 2-6 ordered nodes, e.g.
  Fed (macro) -> Treasury yields (macro, up) -> USD (asset, up) -> Growth stocks (asset, down) ->
  Technology (sector, down) -> NVDA (stock, down). Use node types macro | asset | sector | stock and a direction
  up | down | mixed for the effect on that node.
- rationale: one sentence explaining the call, written in Korean.
- chain node names: write them in Korean (e.g. 연준, 국채금리, 달러, 성장주, 반도체); keep tickers as tickers.

Write every free-text field (rationale, node names, sectors) in Korean.

Typical transmission relationships to reason with (apply only when the headline supports them):
- Hawkish Fed / hot inflation -> higher front-end and long yields -> stronger USD -> pressure on long-duration
  growth equities, small caps, gold, EM/KRW; banks' net interest margins may benefit.
- Dovish Fed / cooling inflation -> lower yields -> weaker USD -> support for tech/growth, small caps, gold, EM.
- Oil supply shocks -> higher energy prices -> inflation expectations up -> energy sector up, airlines/transport
  and consumer discretionary down, rates up.
- AI capex / hyperscaler spending -> semiconductors (GPUs, HBM memory, foundry, networking) -> Korean memory
  makers (Samsung Electronics 005930.KS, SK hynix 000660.KS), power/utilities and data-center supply chain.
- China stimulus -> industrial metals (copper), materials, luxury, Korean exporters; China tensions/export
  controls -> semiconductor equipment and chip makers with China exposure.
- BOJ tightening / yen strength -> carry-trade unwind risk -> global risk-off, Asian equity volatility.
- Korean won weakness -> exporters' earnings translation benefit, foreign outflow risk for KOSPI.
"""

_CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "event_type": {"type": "string"},
                    "sentiment": {"type": "string", "enum": SENTIMENTS},
                    "impact": {"type": "string", "enum": IMPACTS},
                    "importance": {"type": "integer"},
                    "confidence": {"type": "number"},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "sectors": {"type": "array", "items": {"type": "string"}},
                    "chain": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "node": {"type": "string"},
                                "type": {"type": "string", "enum": ["macro", "asset", "sector", "stock"]},
                                "direction": {"type": "string", "enum": ["up", "down", "mixed"]},
                            },
                            "required": ["node", "type", "direction"],
                            "additionalProperties": False,
                        },
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["id", "category", "event_type", "sentiment", "impact", "importance", "confidence",
                             "tickers", "sectors", "chain", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "opportunities": {"type": "array", "items": {"type": "string"}},
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "mixed"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["theme", "direction", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "bullets", "risks", "opportunities", "themes"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = """You write the market interpretation section of an investment research terminal.
Use ONLY the supplied headlines (with their classifications) and the supplied market/macro numbers. Every bullet
must be traceable to that input; cite numbers where available (e.g. "10Y yield +12bp to 4.31%"). Express views as
conditional and probabilistic, never as guaranteed outcomes or personal advice. Prefer 4-6 concise bullets.
Themes must reference the ids of the headlines that support them.
Write all text (headline, bullets, risks, opportunities, theme names) in natural Korean for a Korean investor;
keep tickers, index names and numbers as they are."""


class ClaudeAnalyzer:
    name = "anthropic"

    def __init__(self) -> None:
        self._client = None

    @property
    def enabled(self) -> bool:
        return get_settings().ai_enabled

    @property
    def model(self) -> str:
        return get_settings().ai_model

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=90.0, max_retries=2)
        return self._client

    def _call(self, system: str, user_content: str, schema: dict, max_tokens: int) -> dict | None:
        import anthropic

        s = get_settings()
        client = self._get_client()
        req = dict(
            model=s.ai_model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            output_config={"effort": s.ai_effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if s.ai_refusal_fallback:
                try:
                    resp = client.beta.messages.create(**req, betas=["server-side-fallback-2026-07-01"],
                                                       fallbacks="default")
                except anthropic.BadRequestError as e:
                    if "fallback" not in str(e).lower():
                        raise
                    log.info("server-side fallback not accepted (%s); retrying without it", e)
                    resp = client.messages.create(**req)
            else:
                resp = client.messages.create(**req)
        except anthropic.AuthenticationError as e:
            health.fail(self.name, "authentication failed - check ANTHROPIC_API_KEY")
            log.warning("AI auth error: %s", e)
            return None
        except anthropic.RateLimitError as e:
            health.fail(self.name, "rate limited")
            log.warning("AI rate limited: %s", e)
            return None
        except anthropic.APIStatusError as e:
            health.fail(self.name, f"HTTP {e.status_code}: {e.message}")
            log.warning("AI API error: %s", e)
            return None
        except anthropic.APIConnectionError as e:
            health.fail(self.name, "connection error")
            log.warning("AI connection error: %s", e)
            return None
        if resp.stop_reason == "refusal":
            health.fail(self.name, "model declined the request")
            return None
        if resp.stop_reason == "max_tokens":
            log.warning("AI output truncated at max_tokens")
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            health.fail(self.name, "invalid JSON")
            return None
        health.ok(self.name)
        return data

    def classify(self, items: list[dict]) -> dict[str, dict] | None:
        """items: [{id, title, source, published_at, summary?, tickers?}] -> {id: analysis}"""
        if not self.enabled or not items:
            return None
        payload = json.dumps({"headlines": items}, ensure_ascii=False, default=str)
        data = self._call(SYSTEM_PROMPT, "Classify each headline:\n" + payload, _CLASSIFY_SCHEMA,
                          max_tokens=min(16000, 400 * len(items) + 1000))
        if not data:
            return None
        out = {}
        for it in data.get("items", []):
            it["importance"] = max(0, min(100, int(it.get("importance", 0))))
            it["confidence"] = max(0.0, min(1.0, float(it.get("confidence", 0))))
            out[str(it["id"])] = it
        return out

    def summarize(self, context: dict) -> dict | None:
        if not self.enabled:
            return None
        payload = json.dumps(context, ensure_ascii=False, default=str)
        return self._call(SUMMARY_SYSTEM, "Write the interpretation for this input:\n" + payload, _SUMMARY_SCHEMA,
                          max_tokens=4000)


claude = ClaudeAnalyzer()
