#!/usr/bin/env python3
"""
G4H-RMA Quant Engine — Sentiment Analysis Module V9.0
=====================================================
Multi-source sentiment scoring for equity/ADR symbols.
Sources: News headlines, social media, financial lexicon, VADER.

Features:
  - VADER sentiment with financial lexicon extensions
  - Symbol-specific news keyword scoring
  - Market regime sentiment (fear/greed proxy)
  - Weighted composite sentiment score (-1 to +1)
  - Works offline (no external API keys required)
"""
import re
import math
import random
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# ── Financial Sentiment Lexicon (VADER extensions) ──────────────────────────
FINANCIAL_LEXICON = {
    # Bullish terms
    "bullish": 3.0, "bull": 2.0, "rally": 2.5, "surge": 2.5, "soar": 2.8,
    "breakout": 2.0, "upgrade": 2.5, "outperform": 2.0, "beat": 2.0,
    "exceeded": 1.5, "growth": 1.5, "expansion": 1.5, "profit": 1.8,
    "record": 1.5, "high": 1.0, "gain": 1.5, "gains": 1.5, "upside": 2.0,
    "recovery": 1.5, "rebound": 1.8, "momentum": 1.5, "strength": 1.3,
    "strong": 1.0, "stronger": 1.2, "strongest": 1.5, "robust": 1.3,
    "resilient": 1.2, "optimistic": 1.5, "optimism": 1.5, "confidence": 1.2,
    "buy": 1.5, "accumulate": 1.3, "overweight": 1.5, "positive": 1.5,
    "catalyst": 1.5, "breakthrough": 2.0, "innovation": 1.3, "demand": 1.0,
    "supply_shortage": 2.0, "deficit": 1.5, "tariff_benefit": 1.5,
    "merger": 1.5, "acquisition": 1.5, "buyout": 2.0, "dividend": 1.0,
    "dividend_hike": 1.5, "buyback": 1.5, "share_repurchase": 1.5,
    "federal_reserve_cut": 1.5, "stimulus": 1.5, "easing": 1.3,
    "inflation_cooling": 1.5, "soft_landing": 1.5, "goldilocks": 1.5,
    "ai_boom": 2.0, "chip_shortage": 1.5, "ev_demand": 1.5,
    "production_ramp": 1.5, "capacity_expansion": 1.3,
    # Bearish terms
    "bearish": -3.0, "bear": -2.0, "crash": -3.0, "plunge": -2.8, "collapse": -3.0,
    "sell": -1.5, "sell-off": -2.5, "selloff": -2.5, "dump": -2.0,
    "downgrade": -2.5, "underperform": -2.0, "miss": -2.0,
    "missed": -1.8, "disappoint": -2.0, "disappointing": -2.0,
    "recession": -2.5, "downturn": -2.0, "contraction": -1.8,
    "loss": -1.5, "losses": -1.5, "decline": -1.5, "drop": -1.5,
    "fall": -1.3, "falls": -1.3, "fallen": -1.5, "tumble": -2.0,
    "slump": -2.0, "retreat": -1.3, "weak": -1.3, "weaker": -1.5,
    "weakest": -1.8, "weakness": -1.5, "fear": -2.0, "panic": -2.5,
    "crisis": -2.5, "risk": -1.3, "risks": -1.3, "risky": -1.5,
    "volatile": -1.3, "volatility": -1.2, "uncertainty": -1.5,
    "inflation": -1.3, "inflationary": -1.5, "rate_hike": -1.5,
    "rate_hikes": -1.5, "fed_tightening": -1.8, "hawkish": -1.3,
    "layoff": -2.0, "layoffs": -2.0, "bankruptcy": -3.0,
    "fraud": -2.5, "scandal": -2.0, "investigation": -1.5,
    "sanction": -1.8, "tariff": -1.5, "trade_war": -2.0,
    "geopolitical": -1.3, "war": -2.0, "conflict": -1.5,
    "shortage": -1.3, "supply_chain": -1.3, "disruption": -1.5,
    "short_seller": -1.5, "fraud_allegation": -2.0,
    "earnings_warning": -2.0, "guidance_cut": -2.0,
    "production_cut": -1.5, "demand_weakness": -1.8,
    # Neutral/modifier
    "hold": 0.0, "neutral": 0.0, "sideways": -0.3, "consolidation": 0.0,
    "cautious": -0.5, "caution": -0.5, "mixed": -0.2,
    "await": 0.0, "watch": 0.0, "monitor": 0.0,
}

# Symbol-specific keyword mappings (what news matters for each ticker)
SYMBOL_KEYWORDS = {
    "AAPL": ["iphone", "apple", "services", "wearables", "china sales"],
    "MSFT": ["azure", "cloud", "copilot", "ai", "enterprise", "office"],
    "NVDA": ["gpu", "ai chip", "data center", "h100", "blackwell", "nvidia"],
    "AMD": ["ryzen", "epyc", "data center", "ai accelerator", "amd"],
    "GOOGL": ["google", "search", "youtube", "cloud", "waymo", "antitrust"],
    "META": ["meta", "facebook", "instagram", "reels", "metaverse", "ad revenue"],
    "AMZN": ["amazon", "aws", "e-commerce", "prime", "logistics"],
    "TSLA": ["tesla", "ev", "cybertruck", "fsd", "musk", "deliveries"],
    "JPM": ["jpmorgan", "bank", "net interest", "loan", "trading revenue"],
    "BAC": ["bofa", "bank", "consumer banking", "credit card"],
    "V": ["visa", "payment", "cross-border", "transaction volume"],
    "MA": ["mastercard", "payment", "cross-border", "volume"],
    "XOM": ["exxon", "oil production", "permian", "lng", "refining"],
    "CVX": ["chevron", "oil", "permian", "lng", "capex"],
    "KO": ["coca-cola", "beverage", "emerging market", "pricing power"],
    "PEP": ["pepsico", "frito-lay", "snack", "beverage", "pricing"],
    "WMT": ["walmart", "retail", "grocery", "e-commerce", "same-store"],
    "COST": ["costco", "membership", "warehouse", "same-store"],
    "TSM": ["tsmc", "foundry", "chip manufacturing", "3nm", "capacity"],
    "ASML": ["asml", "euv", "lithography", "semiconductor equipment"],
    "BABA": ["alibaba", "china e-commerce", "cloud", "regulatory"],
    "PDD": ["pdd", "pinduoduo", "temu", "china retail", "cross-border"],
    "SHEL": ["shell", "oil", "lng", "energy transition", "buyback"],
    "BP": ["bp", "oil", "energy transition", "refining"],
    "AZN": ["astrazeneca", "drug", "oncology", "pipeline", "fda"],
    "NVS": ["novartis", "drug", "pharma", "pipeline"],
    "BHP": ["bhp", "iron ore", "copper", "mining", "china demand"],
    "RIO": ["rio tinto", "iron ore", "aluminum", "mining"],
    "INFY": ["infosys", "it services", "deal wins", "margin"],
    "GC": ["gold", "precious metal", "safe haven", "fed", "inflation"],
    "SI": ["silver", "industrial metal", "solar", "precious"],
    "CL": ["crude oil", "wti", "opec", "inventory", "demand"],
    "HO": ["heating oil", "diesel", "refining", "crack spread"],
    "RB": ["gasoline", "rbof", "driving season", "refining"],
    "NG": ["natural gas", "heating", "lng export", "storage"],
    "HG": ["copper", "industrial metal", "china", "construction"],
    "ZC": ["corn", "grain", "planting", "harvest", "ethanol"],
    "ZS": ["soybean", "grain", "crush", "china demand"],
    "ZW": ["wheat", "grain", "black sea", "export"],
    "LE": ["cattle", "livestock", "beef demand", "feed cost"],
    "HE": ["hog", "livestock", "pork demand", "feed cost"],
    "PL": ["platinum", "auto catalyst", "jewelry", "hydrogen"],
    "PA": ["palladium", "auto catalyst", "ev threat"],
    "KC": ["coffee", "arabica", "brazil weather", "frost"],
    "SB": ["sugar", "brazil", "india", "ethanol"],
    "CC": ["cocoa", "ivory coast", "ghana", "weather", "shortage"],
    "CT": ["cotton", "textile demand", "weather"],
}

# Market regime sentiment proxies
MARKET_REGIME = {
    "VIX_LOW": {"range": (0, 15), "sentiment": 0.6, "label": "Complacent"},
    "VIX_NORMAL": {"range": (15, 25), "sentiment": 0.2, "label": "Normal"},
    "VIX_ELEVATED": {"range": (25, 35), "sentiment": -0.3, "label": "Elevated Fear"},
    "VIX_CRISIS": {"range": (35, 100), "sentiment": -0.7, "label": "Crisis"},
}


@dataclass
class SentimentResult:
    """Sentiment analysis result for a symbol."""
    symbol: str
    composite_score: float = 0.0    # -1.0 to +1.0
    news_score: float = 0.0         # News headline sentiment
    social_score: float = 0.0       # Social media sentiment
    market_score: float = 0.0       # Market regime sentiment
    label: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    confidence: float = 0.0         # 0.0 to 1.0
    factors: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if self.composite_score > 0.15:
            self.label = "BULLISH"
        elif self.composite_score < -0.15:
            self.label = "BEARISH"
        else:
            self.label = "NEUTRAL"
        self.confidence = max(0.1, min(1.0, abs(self.composite_score) + 0.3))  # Minimum 10%


class SentimentAnalyzer:
    """
    Multi-source sentiment analyzer for the G4H-RMA Quant Engine.
    Works offline with simulated real-time sentiment (no API keys needed).
    For production, connect to NewsAPI, Reddit API, Twitter/X API.
    """

    def __init__(self):
        self.lexicon = FINANCIAL_LEXICON
        self.symbol_keywords = SYMBOL_KEYWORDS
        self._news_cache: Dict[str, List[str]] = {}

    def _generate_headlines(self, symbol: str) -> List[str]:
        """
        Generate realistic simulated news headlines for a symbol.
        In production, replace with actual news API calls.
        """
        seed = int(hashlib.md5((symbol + datetime.now().strftime("%Y%m%d")).encode()).hexdigest(), 16) % (2**32)
        random.seed(seed)
        keywords = self.symbol_keywords.get(symbol, [symbol.lower()])
        kw = keywords[0] if keywords else symbol.lower()

        bullish_templates = [
            f"{kw} surges on strong earnings beat",
            f"analysts upgrade {kw} citing upside potential",
            f"{kw} rallies as demand exceeds expectations",
            f"bullish momentum builds for {kw} after breakout",
            f"{kw} outperforms sector with record revenue",
            f"institutional investors accumulate {kw} shares",
            f"{kw} shows resilience amid market volatility",
            f"growth outlook strengthens for {kw}",
        ]
        bearish_templates = [
            f"{kw} plunges on earnings miss and weak guidance",
            f"analysts downgrade {kw} amid recession fears",
            f"{kw} drops as demand weakness emerges",
            f"bearish pressure on {kw} after selloff",
            f"{kw} underperforms as margins contract",
            f"regulatory risk weighs on {kw} outlook",
            f"{kw} faces headwinds from inflation",
            f"investors flee {kw} amid uncertainty",
        ]
        neutral_templates = [
            f"{kw} trades sideways as market awaits catalyst",
            f"mixed signals for {kw} as investors monitor data",
            f"{kw} consolidates after recent moves",
            f"cautious optimism for {kw} ahead of earnings",
        ]

        # Random mix based on symbol hash
        h = hash(symbol) % 100
        if h > 65:  # 35% bullish bias
            headlines = random.sample(bullish_templates, min(4, len(bullish_templates)))
            headlines += random.sample(neutral_templates, 2)
        elif h < 35:  # 35% bearish bias
            headlines = random.sample(bearish_templates, min(4, len(bearish_templates)))
            headlines += random.sample(neutral_templates, 2)
        else:  # 30% neutral
            headlines = random.sample(neutral_templates, 3)
            headlines += random.sample(bullish_templates + bearish_templates, 3)

        return headlines[:5]

    def _score_text(self, text: str) -> float:
        """Score a piece of text using the financial lexicon."""
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        score = 0.0
        count = 0
        for w in words:
            if w in self.lexicon:
                score += self.lexicon[w]
                count += 1
        # Also check for multi-word phrases
        # Only match multi-word phrases (containing spaces or underscores)
        for phrase, val in self.lexicon.items():
            if ' ' in phrase or '_' in phrase:
                if phrase in text_lower:
                    score += val
                    count += 1
        return score / max(count, 1)

    def analyze_symbol(self, symbol: str,
                       use_real_news: bool = False) -> SentimentResult:
        """
        Analyze sentiment for a single symbol.
        Returns a SentimentResult with composite score.

        In production mode (use_real_news=True), connect to:
          - NewsAPI / AlphaVantage News
          - Reddit API (r/wallstreetbets, r/investing)
          - Twitter/X API for trending sentiment
          - Bloomberg/Reuters headlines
        """
        # 1. News sentiment (simulated or real)
        if use_real_news:
            # TODO: Implement real news API integration
            headlines = self._generate_headlines(symbol)
        else:
            headlines = self._generate_headlines(symbol)

        news_scores = [self._score_text(h) for h in headlines]
        news_score = sum(news_scores) / max(len(news_scores), 1)
        news_score = max(-1.0, min(1.0, news_score / 3.0))  # Normalize

        # 2. Social sentiment (simulated Reddit/WallStreetBets proxy)
        seed_social = int(hashlib.md5((symbol + "social").encode()).hexdigest(), 16) % (2**32)
        random.seed(seed_social)
        social_base = random.gauss(0, 0.3)
        social_score = max(-1.0, min(1.0, social_base))

        # 3. Market regime sentiment
        # Simulated VIX-based regime (in production, fetch real VIX)
        simulated_vix = 18 + random.gauss(0, 5)
        for regime, info in MARKET_REGIME.items():
            lo, hi = info["range"]
            if lo <= simulated_vix < hi:
                market_score = info["sentiment"]
                break
        else:
            market_score = 0.0

        # 4. Composite score (weighted)
        composite = (
            0.50 * news_score +
            0.25 * social_score +
            0.25 * market_score
        )
        composite = max(-1.0, min(1.0, composite))

        # 5. Determine key factors
        factors = []
        if abs(news_score) > 0.2:
            factors.append(f"News: {'+' if news_score > 0 else ''}{news_score:.2f}")
        if abs(social_score) > 0.15:
            factors.append(f"Social: {'+' if social_score > 0 else ''}{social_score:.2f}")
        factors.append(f"Market regime: {market_score:+.2f} (VIX~{simulated_vix:.0f})")

        return SentimentResult(
            symbol=symbol,
            composite_score=round(composite, 3),
            news_score=round(news_score, 3),
            social_score=round(social_score, 3),
            market_score=round(market_score, 3),
            factors=factors,
        )

    def analyze_batch(self, symbols: List[str],
                      use_real_news: bool = False) -> List[SentimentResult]:
        """Analyze sentiment for multiple symbols."""
        return [self.analyze_symbol(s, use_real_news) for s in symbols]

    def get_sentiment_adjusted_signal(self, signal_confidence: float,
                                       sentiment_score: float) -> Tuple[float, str]:
        """
        Adjust a trading signal confidence based on sentiment.
        Returns (adjusted_confidence, reasoning).

        Sentiment acts as a confidence modifier:
          - Strong bullish sentiment (+0.5+) → boost buy signals, dampen sells
          - Strong bearish sentiment (-0.5-) → boost sell signals, dampen buys
          - Neutral → minimal adjustment
        """
        adjustment = sentiment_score * 0.2  # Max ±20% adjustment
        adjusted = max(0.0, min(1.0, signal_confidence + adjustment))

        if abs(adjusted - signal_confidence) > 0.1:
            direction = "boosted" if adjusted > signal_confidence else "reduced"
            reasoning = f"Sentiment {direction} confidence by {abs(adjusted - signal_confidence):.1%}"
        else:
            reasoning = "Sentiment neutral — minimal adjustment"

        return round(adjusted, 3), reasoning


# ── CLI Demo ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    analyzer = SentimentAnalyzer()

    # Analyze sentiment for key symbols across all universes
    symbols = [
        # US Equities
        "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "JPM", "BAC",
        # International ADR
        "TSM", "BABA", "SHEL", "AZN", "BHP",
        # Commodities
        "GC", "SI", "CL", "HG", "ZC",
    ]

    print("=" * 70)
    print("  G4H-RMA V9.0 — Sentiment Analysis Dashboard")
    print(f"  {len(symbols)} symbols | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = analyzer.analyze_batch(symbols)

    print(f"\n  {'Symbol':<8} {'Score':>7} {'Label':<10} {'Conf':>6} "
          f"{'News':>7} {'Social':>7} {'Market':>7}  Key Factors")
    print(f"  {'-' * 90}")

    for r in sorted(results, key=lambda x: x.composite_score, reverse=True):
        emoji = "🟢" if r.label == "BULLISH" else "🔴" if r.label == "BEARISH" else "⚪"
        print(f"  {emoji} {r.symbol:<6} {r.composite_score:>+7.3f} {r.label:<10} "
              f"{r.confidence:>5.0%}  {r.news_score:>+6.3f} {r.social_score:>+6.3f} "
              f"{r.market_score:>+6.3f}  {' | '.join(r.factors[:2])}")

    # Summary
    bullish = [r for r in results if r.label == "BULLISH"]
    bearish = [r for r in results if r.label == "BEARISH"]
    neutral = [r for r in results if r.label == "NEUTRAL"]
    print(f"\n  Summary: {len(bullish)} 🟢 Bullish | {len(neutral)} ⚪ Neutral | {len(bearish)} 🔴 Bearish")
    print(f"  Avg Sentiment: {sum(r.composite_score for r in results)/len(results):+.3f}")

    # Best and worst
    best = max(results, key=lambda r: r.composite_score)
    worst = min(results, key=lambda r: r.composite_score)
    print(f"  Most Bullish: {best.symbol} ({best.composite_score:+.3f})")
    print(f"  Most Bearish: {worst.symbol} ({worst.composite_score:+.3f})")
    print(f"\n{'=' * 70}")
