# utils.py
import re


SUPPORTED_ASSETS = {"BTC", "ETH", "EURC"}  # SOL has no contract address; EURC is supported by the executor


def parse_ai_decision(raw_response: str) -> dict:
    """
    Parse the agent's response into a stable trade instruction.

    Expected examples:
    DECISION: BUY ETH
    DECISION: SELL BTC
    DECISION: HOLD
    """
    text = (raw_response or "").upper()
    decision_match = re.search(r"^\s*DECISION:[^\S\r\n]*(BUY|SELL|HOLD)\b(?:[^\S\r\n]+([A-Z]+))?", text, re.MULTILINE)

    if not decision_match:
        return {"decision": "HOLD", "asset": None, "reason": raw_response or "No decision found."}

    decision = decision_match.group(1)
    asset = decision_match.group(2)

    reason_match = re.search(r"REASON:\s*(.+)", raw_response or "", re.IGNORECASE | re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else ""

    if decision in {"BUY", "SELL"} and asset not in SUPPORTED_ASSETS:
        return {
            "decision": "HOLD",
            "asset": None,
            "reason": reason or f"Unsupported or missing asset for {decision}: {asset or 'none'}.",
        }

    return {"decision": decision, "asset": asset, "reason": reason}
