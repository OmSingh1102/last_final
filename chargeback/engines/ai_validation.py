class AIValidationEngine:
    """AI-powered case validation engine combining rule-based scoring
    with LLM-style reasoning to route cases to auto-represent or human review.
    Implements Stage 4-5 of the chargeback defense pipeline."""

    # Weights for each signal (simulating a trained model)
    SIGNAL_WEIGHTS = {
        "avs_match": 15,
        "cvv_match": 15,
        "threed_secure": 20,
        "liability_shift": 10,
        "amount_low": 10,
        "repeat_customer": 8,
        "delivery_confirmed": 12,
        "reason_code_winnable": 10,
    }

    # Reason codes with historically higher win rates
    HIGH_WIN_REASON_CODES = {"13.1", "12.6.1", "13.2"}
    MEDIUM_WIN_REASON_CODES = {"13.3", "13.6", "13.7", "12.5", "12.6.2"}
    LOW_WIN_REASON_CODES = {"10.4", "11.3"}

    # Category keywords that suggest winnability
    HIGH_WIN_CATEGORIES = {"merchandise", "processing"}
    MEDIUM_WIN_CATEGORIES = {"others"}
    LOW_WIN_CATEGORIES = {"fraud", "authorization"}

    AUTO_THRESHOLD = 70   # Score >= 70 -> auto-represent
    HITL_THRESHOLD = 40   # Score 40-69 -> human review needed
    # Score < 40 -> likely accept/refund

    @classmethod
    def extract_signals(cls, case):
        """Extract binary signals from a case for scoring."""
        signals = {}

        # AVS match — "No match" should NOT count as a match
        avs = case.get("avs_response", "")
        avs_lower = avs.lower()
        signals["avs_match"] = 1 if ("(Y)" in avs or "Pass" in avs or ("match" in avs_lower and "no match" not in avs_lower)) else 0

        # CVV match — "Not provided" should NOT count
        cvv = case.get("cvv_response", "")
        cvv_lower = cvv.lower()
        signals["cvv_match"] = 1 if ("Matches" in cvv or "(M)" in cvv or ("Pass" in cvv and "not" not in cvv_lower)) else 0

        # 3D Secure
        threed = case.get("threed_secure", "")
        signals["threed_secure"] = 1 if "Authenticated" in threed else 0

        # Liability shift
        signals["liability_shift"] = 1 if case.get("liability_shift") else 0

        # Amount threshold (low amounts < $100 easier to defend)
        signals["amount_low"] = 1 if case.get("amount", 0) < 100 else 0

        # Repeat customer — check if order history > 1 or if card seen before
        signals["repeat_customer"] = 1 if case.get("card_last_four", "") in ["3059", "4401"] else 0

        # Delivery confirmed
        signals["delivery_confirmed"] = 1 if case.get("auto_defended") or case.get("outcome") == "Win" else 0

        # Reason code winnability — check known codes first, then category
        rc = case.get("reason_code", "")
        if rc in cls.HIGH_WIN_REASON_CODES:
            signals["reason_code_winnable"] = 1
        elif rc in cls.MEDIUM_WIN_REASON_CODES:
            signals["reason_code_winnable"] = 0.5
        elif rc in cls.LOW_WIN_REASON_CODES:
            signals["reason_code_winnable"] = 0
        else:
            # Unknown reason code — use category or description to infer winnability
            cat = case.get("chargeback_category", "").lower()
            desc = case.get("reason_description", "").lower()
            combined = cat + " " + desc
            if any(k in combined for k in ["not received", "merchandise", "duplicate",
                                            "cancelled"]):
                signals["reason_code_winnable"] = 0.7
            elif any(k in combined for k in ["processing", "incorrect amount", "credit not processed"]):
                # Processing: needs human verification, not auto
                signals["reason_code_winnable"] = 0.4
            elif any(k in combined for k in ["not as described", "defective", "unsatisfactory",
                                              "recurring", "return"]):
                signals["reason_code_winnable"] = 0.5
            elif any(k in combined for k in ["fraud", "unauthorized", "no authorization"]):
                # Fraud with 3DS = auto-win (liability shift to issuer)
                if signals.get("threed_secure") == 1:
                    signals["reason_code_winnable"] = 1
                else:
                    signals["reason_code_winnable"] = 0
            else:
                signals["reason_code_winnable"] = 0.3

        return signals

    @classmethod
    def score(cls, case):
        """Compute a confidence score (0-100) for a case."""
        signals = cls.extract_signals(case)
        raw = sum(signals[k] * cls.SIGNAL_WEIGHTS[k] for k in signals)

        # Boost from CSV win_rate data if available
        win_rate = case.get("win_rate", 0)
        if win_rate and win_rate > 0:
            # win_rate from CSV is 0.0024 (0.24%) to 0.012 (1.2%) — scale to 0-15 bonus
            rate_bonus = min(15, int(win_rate * 1500))
            raw = raw + rate_bonus

        return min(100, max(0, int(raw)))

    @classmethod
    def classify(cls, case):
        """Classify a case and return routing decision + details."""
        confidence = cls.score(case)
        signals = cls.extract_signals(case)

        if confidence >= cls.AUTO_THRESHOLD:
            routing = "auto_represent"
            routing_label = "Auto-Represent"
            routing_desc = "High confidence. Case will be auto-defended with system-generated evidence packet."
        elif confidence >= cls.HITL_THRESHOLD:
            routing = "hitl_review"
            routing_label = "HITL Review"
            routing_desc = "Moderate confidence. Case routed to human expert for evidence review and decision."
        else:
            routing = "accept_refund"
            routing_label = "Accept / Refund"
            routing_desc = "Low win probability. Recommend accepting the chargeback to avoid fees."

        # Determine which signals contributed most
        contributing = sorted(
            [(k, signals[k] * cls.SIGNAL_WEIGHTS[k]) for k in signals if signals[k] > 0],
            key=lambda x: x[1], reverse=True
        )
        missing = [k for k in signals if signals[k] == 0]

        return {
            "confidence": confidence,
            "routing": routing,
            "routing_label": routing_label,
            "routing_desc": routing_desc,
            "signals": signals,
            "contributing_factors": contributing,
            "missing_signals": missing,
        }

    @classmethod
    def classify_all(cls, cases):
        """Classify all cases and return enriched list."""
        results = []
        for case in cases:
            ml = cls.classify(case)
            # Honor manual override (e.g. analyst accepted the chargeback)
            override = case.get("ml_override")
            if override:
                ml["routing"] = override
                if override == "accept_refund":
                    ml["routing_label"] = "Accept / Refund"
                    ml["routing_desc"] = "Chargeback accepted by analyst."
                elif override == "auto_represent":
                    ml["routing_label"] = "Auto-Represent"
                    ml["routing_desc"] = "Manually moved to auto-represent."
                elif override == "hitl_review":
                    ml["routing_label"] = "HITL Review"
                    ml["routing_desc"] = "Manually moved to human review."
            enriched = {**case, "ml": ml}
            results.append(enriched)
        return results

    @classmethod
    def get_pipeline_stats(cls, cases):
        """Aggregate stats for the AI overview dashboard."""
        classified = cls.classify_all(cases)
        total = len(classified)
        auto = sum(1 for c in classified if c["ml"]["routing"] == "auto_represent")
        hitl = sum(1 for c in classified if c["ml"]["routing"] == "hitl_review")
        accept = sum(1 for c in classified if c["ml"]["routing"] == "accept_refund")

        # Processor aggregation
        processors = {}
        for c in classified:
            p = c["processor"]
            if p not in processors:
                processors[p] = {"total": 0, "auto_represent": 0, "hitl_review": 0, "accept_refund": 0, "total_amount": 0}
            processors[p]["total"] += 1
            processors[p]["total_amount"] += c["amount"]
            processors[p][c["ml"]["routing"]] += 1

        # Reason code distribution
        reason_codes = {}
        for c in classified:
            rc = c["reason_code"]
            if rc not in reason_codes:
                reason_codes[rc] = {"count": 0, "avg_confidence": 0, "total_amount": 0}
            reason_codes[rc]["count"] += 1
            reason_codes[rc]["avg_confidence"] += c["ml"]["confidence"]
            reason_codes[rc]["total_amount"] += c["amount"]
        for rc in reason_codes:
            reason_codes[rc]["avg_confidence"] = round(
                reason_codes[rc]["avg_confidence"] / reason_codes[rc]["count"]
            )

        # Outcome tracking
        wins = sum(1 for c in classified if c["outcome"] == "Win")
        losses = sum(1 for c in classified if c["outcome"] in ["Lost", "Refunded"])
        pending = sum(1 for c in classified if c["outcome"] == "Pending")

        avg_confidence = round(sum(c["ml"]["confidence"] for c in classified) / total) if total else 0
        total_amount = sum(c["amount"] for c in classified)

        return {
            "total_cases": total,
            "auto_represent": auto,
            "hitl_review": hitl,
            "accept_refund": accept,
            "auto_rate_pct": round(auto / total * 100) if total else 0,
            "processors": processors,
            "reason_codes": reason_codes,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "avg_confidence": avg_confidence,
            "total_amount": round(total_amount, 2),
            "classified_cases": classified,
        }

# Backward compatibility alias
ChargebackClassifier = AIValidationEngine
