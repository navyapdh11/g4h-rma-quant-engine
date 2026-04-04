"""
Alibaba DashScope AI Agent — Qianwen (通义千问) Integration
============================================================
Provides AI-powered features:
  - Trade signal analysis and explanation
  - Market sentiment analysis
  - Natural language trade summaries
  - Risk assessment commentary

API: https://dashscope.console.aliyun.com/
Model: qwen-turbo / qwen-plus / qwen-max
"""
from __future__ import annotations
import asyncio
import logging
import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class DashScopeAIAgent:
    """
    Alibaba DashScope AI Agent for trading insights.
    
    Uses Qianwen (Qwen) LLM for:
    - Analyzing trading signals
    - Generating trade summaries
    - Risk assessment
    - Market commentary
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = "https://dashscope.aliyuncs.com/api/v1"
        self.model = "qwen-turbo"  # Can be qwen-plus, qwen-max
        self._available = bool(self.api_key)
        
        if not self._available:
            logger.warning("DashScope API key not configured — AI features disabled")
    
    def is_available(self) -> bool:
        """Check if AI agent is available."""
        return self._available
    
    async def analyze_signal(
        self,
        pair: str,
        signal: str,
        confidence: float,
        kalman_z: float,
        egarch_regime: str,
        mcts_ev: float,
    ) -> Dict[str, Any]:
        """
        Analyze a trading signal using AI.
        
        Returns:
            AI analysis with reasoning and recommendations
        """
        if not self._available:
            return self._fallback_analysis(signal, confidence)
        
        prompt = self._build_signal_prompt(
            pair, signal, confidence, kalman_z, egarch_regime, mcts_ev
        )
        
        try:
            response = await self._call_dashscope(prompt)
            return self._parse_analysis(response)
        except Exception as e:
            logger.error(f"DashScope analysis failed: {e}")
            return self._fallback_analysis(signal, confidence)
    
    def _build_signal_prompt(
        self,
        pair: str,
        signal: str,
        confidence: float,
        kalman_z: float,
        egarch_regime: str,
        mcts_ev: float,
    ) -> str:
        """Build prompt for signal analysis."""
        return f"""You are an expert quantitative trading analyst. Analyze this trading signal:

**Pair**: {pair}
**Signal**: {signal}
**Confidence**: {confidence * 100:.1f}%
**Kalman Z-Score**: {kalman_z:.2f}
**Volatility Regime **(EGARCH) {egarch_regime}
**MCTS Expected Value**: {mcts_ev:.3f}

Provide a concise analysis (max 150 words) covering:
1. Signal strength assessment
2. Risk considerations based on volatility regime
3. Recommended position sizing
4. Any warnings or caveats

Format as JSON:
{{
  "assessment": "STRONG" | "MODERATE" | "WEAK",
  "reasoning": "brief explanation",
  "position_size": "FULL" | "HALF" | "QUARTER",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "warnings": ["list any concerns"]
}}"""
    
    async def _call_dashscope(self, prompt: str, max_tokens: int = 300) -> str:
        """Call DashScope API."""
        import aiohttp
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert quantitative trading analyst. Respond in JSON format."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            },
            "parameters": {
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "result_format": "message"
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/services/aigc/text-generation/generation",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"DashScope API error: {response.status} - {error_text}")
                
                result = await response.json()
                return result["output"]["choices"][0]["message"]["content"]
    
    def _parse_analysis(self, response: str) -> Dict[str, Any]:
        """Parse AI response."""
        try:
            # Try to extract JSON from response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                analysis = json.loads(json_str)
            else:
                analysis = {
                    "assessment": "MODERATE",
                    "reasoning": response[:200],
                    "position_size": "HALF",
                    "risk_level": "MEDIUM",
                    "warnings": []
                }
            
            analysis["source"] = "dashscope_ai"
            analysis["timestamp"] = datetime.now(timezone.utc).isoformat()
            return analysis
            
        except json.JSONDecodeError:
            logger.warning("Failed to parse AI response as JSON")
            return {
                "assessment": "MODERATE",
                "reasoning": response[:200],
                "position_size": "HALF",
                "risk_level": "MEDIUM",
                "warnings": ["AI response parsing failed"],
                "source": "dashscope_ai"
            }
    
    def _fallback_analysis(self, signal: str, confidence: float) -> Dict[str, Any]:
        """Fallback analysis when AI unavailable."""
        if confidence > 0.7:
            assessment = "STRONG"
            position = "FULL"
        elif confidence > 0.5:
            assessment = "MODERATE"
            position = "HALF"
        else:
            assessment = "WEAK"
            position = "QUARTER"
        
        return {
            "assessment": assessment,
            "reasoning": f"Signal: {signal}, Confidence: {confidence*100:.0f}%. Rule-based analysis (AI unavailable).",
            "position_size": position,
            "risk_level": "MEDIUM" if confidence > 0.5 else "HIGH",
            "warnings": ["AI analysis unavailable — using rule-based assessment"],
            "source": "fallback"
        }
    
    async def generate_trade_summary(
        self,
        pair: str,
        action: str,
        qty: int,
        price_base: float,
        price_quote: float,
        beta: float,
        pnl_estimate: float,
    ) -> str:
        """Generate natural language trade summary."""
        if not self._available:
            return self._fallback_trade_summary(pair, action, qty, price_base, price_quote)
        
        prompt = f"""Summarize this trade in one sentence:

**Pair**: {pair}
**Action**: {action}
**Quantity**: {qty} units
**Prices**: {price_base:.2f} / {price_quote:.2f}
**Hedge Ratio **(beta) {beta:.3f}
**Est. PnL**: {pnl_estimate:.2f}

Provide a concise, professional trade summary."""
        
        try:
            summary = await self._call_dashscope(prompt, max_tokens=100)
            return summary.strip()
        except Exception:
            return self._fallback_trade_summary(pair, action, qty, price_base, price_quote)
    
    def _fallback_trade_summary(
        self, pair: str, action: str, qty: int, price_base: float, price_quote: float
    ) -> str:
        """Fallback trade summary."""
        return f"Executed {action} on {pair}: {qty} units @ ${price_base:.2f}/${price_quote:.2f}"
    
    async def market_commentary(
        self,
        volatility_regime: str,
        vix_level: Optional[float] = None,
        market_sentiment: Optional[str] = None,
    ) -> str:
        """Generate market commentary based on conditions."""
        if not self._available:
            return f"Current volatility regime: {volatility_regime}. AI commentary unavailable."
        
        prompt = f"""Provide a brief market commentary (2-3 sentences) for a quantitative trader:

**Volatility Regime**: {volatility_regime}
**VIX Level**: {vix_level or 'N/A'}
**Market Sentiment**: {market_sentiment or 'N/A'}

Focus on implications for pairs trading strategy."""
        
        try:
            commentary = await self._call_dashscope(prompt, max_tokens=150)
            return commentary.strip()
        except Exception:
            return f"Volatility regime: {volatility_regime}. Consider adjusting position sizes accordingly."


# Global AI agent instance
_ai_agent: Optional[DashScopeAIAgent] = None


def get_ai_agent() -> DashScopeAIAgent:
    """Get or create the AI agent."""
    global _ai_agent
    if _ai_agent is None:
        _ai_agent = DashScopeAIAgent()
    return _ai_agent
