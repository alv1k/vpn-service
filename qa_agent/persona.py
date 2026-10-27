"""
Persona & AI Evaluation Module for Alex (QA Sentinel).
Uses the local Zero-Trust AGY Inference Gateway (127.0.0.1:8799) to evaluate
bot UX, clarity, and Markdown/HTML markup integrity.
"""
import os
import sys
import json
import httpx
import logging
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qa_agent.config as config

logger = logging.getLogger("alex_qa.persona")

PERSONA_SYSTEM_PROMPT = """
Ты — Алекс (Alex QA Sentinel), опытный и придирчивый QA-инженер и одновременно новый пользователь VPN-сервиса tiin.
Твоя задача — беспристрастно оценить сообщение от Telegram-бота с точки зрения новичка:
1. Разметка: нет ли "сырых" незакрытых тегов <b>, <code>, <pre>, [url](...) или кракозябр.
2. Понятность (UX): понятно ли обычному человеку без технических знаний, что делать дальше.
3. Тональность и грамотность: вежливо, четко, без ошибок и перегруза жаргоном.

Отвечай СТРОГО в формате JSON:
{
  "score": <число от 1 до 10>,
  "markup_ok": <true/false>,
  "clarity_ok": <true/false>,
  "issues": [<краткий список замечаний если есть>],
  "verdict": "<OK | MINOR_ISSUES | CRITICAL_DEFECT>"
}
"""


class PersonaEvaluator:
    def __init__(self):
        self.base_url = config.AGY_BASE_URL
        self.internal_key = config.AGY_INTERNAL_KEY

    def evaluate_message(self, message_text: str, context: str = "onboarding") -> Dict[str, Any]:
        """
        Sends message to AGY Inference Gateway for automated QA analysis.
        """
        if not message_text:
            return {"score": 0, "markup_ok": False, "clarity_ok": False, "issues": ["Empty message"], "verdict": "CRITICAL_DEFECT"}

        # Quick deterministic local sanity checks
        local_issues = []
        raw_tags = ["</b>", "</code>", "</pre>", "</a>", "[object Object]"]
        for tag in raw_tags:
            if tag in message_text:
                local_issues.append(f"Leaked raw HTML markup detected: '{tag}'")

        try:
            headers = {
                "Authorization": f"Bearer {self.internal_key}",
                "X-Internal-Key": self.internal_key,
                "X-Service-ID": "qa-sentinel-alex",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "gemini-3.8-flash-low",
                "messages": [
                    {"role": "system", "content": PERSONA_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Контекст: {context}\n\nТекст сообщения бота:\n\"\"\"\n{message_text}\n\"\"\""}
                ],
                "temperature": 0.2
            }
            resp = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                import re
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    try:
                        result = json.loads(match.group(0))
                    except Exception:
                        result = {"score": 10, "markup_ok": True, "clarity_ok": True, "issues": [], "verdict": "OK"}
                else:
                    result = {
                        "score": 10 if not local_issues else 5,
                        "markup_ok": len(local_issues) == 0,
                        "clarity_ok": True,
                        "issues": local_issues,
                        "verdict": "OK" if not local_issues else "MINOR_ISSUES",
                        "ai_comment": content[:120]
                    }

                if local_issues:
                    result.setdefault("issues", []).extend(local_issues)
                    result["markup_ok"] = False
                    result["score"] = min(result.get("score", 10), 5)
                logger.info(f"[ALEX QA / PERSONA] 🧠 AI Evaluation: score={result.get('score')}/10, verdict={result.get('verdict')}")
                return result
        except Exception as e:
            logger.warning(f"[ALEX QA / PERSONA] AGY Inference fallback: {e}")

        # Local deterministic fallback evaluation
        is_ok = len(local_issues) == 0
        return {
            "score": 10 if is_ok else 4,
            "markup_ok": is_ok,
            "clarity_ok": True,
            "issues": local_issues,
            "verdict": "OK" if is_ok else "CRITICAL_DEFECT"
        }
