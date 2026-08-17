"""Manager Hub chart data.

Six groups, modelled on the client CB reporting deck:
  1. Card network / processor breakdown
  2. Performance metrics  (won / lost / not fought / pending, fought vs not fought)
  3. CB type              (dispute stage: retrieval, chargeback, pre-arbitration)
  4. Geography            (top delivery regions and countries)
  5. Top reason codes     (per card network)
  6. Volume trends        (daily / weekly / monthly, filterable)

Everything is computed server-side; the template draws with CSS/SVG so the
page needs no external chart library.

Money is never summed across currencies. The working sheet carries IDR, USD and
NGN side by side, so every amount in here is a `{currency: total}` dict rather
than one number — adding them would produce a figure that means nothing.
"""
from collections import Counter, defaultdict, OrderedDict
from datetime import timedelta

from chargeback.utils.datetime_helpers import safe_float, parse_any_datetime

# Stable colours so a network keeps the same colour in every chart.
NETWORK_COLORS = OrderedDict([
    ("Visa", "#f4611a"),
    ("Mastercard", "#b45309"),
    ("Amex", "#e8a33d"),
    ("Discover", "#9a3412"),
    ("Klarna", "#d97757"),
    ("Other", "#c9a898"),
])

OUTCOME_COLORS = {
    "Won": "#2e7d32",
    "Lost": "#c62828",
    "Not Fought": "#b45309",
    "Decision Pending": "#f4611a",
}

# The deck's four case states, in the order it presents them.
STATUS_ORDER = ["Decision Pending", "Lost", "Not Fought", "Won"]

# Dispute-stage codes as the deck names them. Anything not listed keeps its raw
# value rather than being folded into an "Other" bucket that hides it.
STAGE_LABELS = {
    "Request for Information": "Retrieval Request",
    "RFI": "Retrieval Request",
    "Chargeback": "1st Chargeback / Dispute",
    "CB": "1st Chargeback / Dispute",
    "Pre-Arbitration": "Second Chargeback / Pre-Arbitration",
    "PRE_ARB": "Second Chargeback / Pre-Arbitration",
}
STAGE_ORDER = ["Retrieval Request", "1st Chargeback / Dispute",
               "Second Chargeback / Pre-Arbitration"]

# Delivery rows with no physical destination.
DIGITAL_REGION = "N/A (Digital)"

TOP_N_REGIONS = 5
TOP_N_REASONS = 5


def _by_currency(cases):
    """Total amount per currency. Never a single cross-currency number."""
    totals = defaultdict(float)
    for c in cases:
        totals[(c.get("currency") or "USD").strip() or "USD"] += safe_float(c.get("amount"))
    return {cur: round(v, 2) for cur, v in sorted(totals.items())}


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else 0.0


class ManagerCharts:

    @classmethod
    def compute(cls, classified, orders=None):
        """classified: cases carrying an `ml` dict (from AIValidationEngine).

        `orders` is accepted but unused since risk monitoring was removed; the
        caller still passes the order book, so the parameter stays rather than
        breaking that call site.
        """
        if not classified:
            return {"has_data": False}

        total = len(classified)

        # ── 1. Card network / processor breakdown ───────────────────────────
        # Cases are bucketed rather than counted so each bucket's money can be
        # split by currency afterwards.
        net_cases, proc_cases = defaultdict(list), defaultdict(list)
        proc_by_net = defaultdict(Counter)

        for c in classified:
            net = cls._network(c)
            proc = c.get("processor") or "Unknown"
            net_cases[net].append(c)
            proc_cases[proc].append(c)
            proc_by_net[proc][net] += 1

        net_count = Counter({n: len(v) for n, v in net_cases.items()})
        networks = [n for n in NETWORK_COLORS if n in net_count]
        networks += sorted(n for n in net_count if n not in NETWORK_COLORS)

        network_rows = [{
            "name": n,
            "color": NETWORK_COLORS.get(n, "#c9a898"),
            "count": net_count[n],
            "amounts": _by_currency(net_cases[n]),
            "pct": _pct(net_count[n], total),
        } for n in networks]

        # Pie slices need cumulative angles for the CSS conic-gradient.
        cls._add_arcs(network_rows)

        processor_rows = []
        for p, cases in sorted(proc_cases.items(), key=lambda kv: -len(kv[1])):
            cnt = len(cases)
            mix = [{
                "name": n,
                "color": NETWORK_COLORS.get(n, "#c9a898"),
                "count": proc_by_net[p][n],
                "pct": _pct(proc_by_net[p][n], cnt),
            } for n in networks if proc_by_net[p][n]]
            processor_rows.append({
                "name": p,
                "count": cnt,
                "amounts": _by_currency(cases),
                "pct": _pct(cnt, total),
                "mix": mix,
            })

        # ── 2. Performance metrics ──────────────────────────────────────────
        # "Fought" = the case was represented. "Not fought" = conceded to the
        # cardholder — a decision in its own right, so it stays separate from
        # Lost rather than being folded into it.
        status_cases = defaultdict(list)
        status_by_net = defaultdict(Counter)
        fought_cases, not_fought_cases = [], []

        for c in classified:
            status = cls._status(c)
            status_cases[status].append(c)
            status_by_net[status][cls._network(c)] += 1
            (fought_cases if cls._is_fought(c) else not_fought_cases).append(c)

        won = len(status_cases["Won"])
        lost = len(status_cases["Lost"])
        conceded = len(status_cases["Not Fought"])
        pending = len(status_cases["Decision Pending"])
        decided = won + lost + conceded

        status_stacks = []
        for s in STATUS_ORDER:
            col_total = sum(status_by_net[s].values())
            status_stacks.append({
                "name": s,
                "total": col_total,
                "color": OUTCOME_COLORS[s],
                "segments": [{
                    "name": n,
                    "color": NETWORK_COLORS.get(n, "#c9a898"),
                    "count": status_by_net[s][n],
                    "pct": _pct(status_by_net[s][n], col_total),
                } for n in networks if status_by_net[s][n]],
            })

        # Per-network rows for the table under the stacked columns.
        status_table = [{
            "name": n,
            "color": NETWORK_COLORS.get(n, "#c9a898"),
            "cells": [status_by_net[s][n] for s in STATUS_ORDER],
            "total": sum(status_by_net[s][n] for s in STATUS_ORDER),
        } for n in networks]

        recovered_rows = [
            {"name": s, "color": OUTCOME_COLORS[s], "count": len(status_cases[s]),
             "amounts": _by_currency(status_cases[s]), "pct": _pct(len(status_cases[s]), total)}
            for s in ("Won", "Lost", "Not Fought", "Decision Pending")
        ]
        cls._add_arcs(recovered_rows)

        fought_rows = [
            {"name": "Fought", "color": "#f4611a", "count": len(fought_cases),
             "amounts": _by_currency(fought_cases), "pct": _pct(len(fought_cases), total)},
            {"name": "Not Fought", "color": "#e8dad0", "count": len(not_fought_cases),
             "amounts": _by_currency(not_fought_cases), "pct": _pct(len(not_fought_cases), total)},
        ]
        cls._add_arcs(fought_rows)

        # ── 3. CB type (dispute stage) ──────────────────────────────────────
        stages = cls._build_stages(classified, total)

        # ── 4. Geography ────────────────────────────────────────────────────
        geography = cls._build_geography(classified, networks, total)

        # ── 5. Top reason codes per network ─────────────────────────────────
        reason_panels = cls._build_reason_panels(net_cases, networks)

        # ── 6. Volume trends ────────────────────────────────────────────────
        trend = cls._build_trend(classified, networks)

        return {
            "has_data": True,
            "total": total,
            "networks": networks,
            "network_colors": {n: NETWORK_COLORS.get(n, "#c9a898") for n in networks},
            "currencies": sorted({(c.get("currency") or "USD").strip() or "USD"
                                  for c in classified}),
            "breakdown": {
                "network_rows": network_rows,
                "processor_rows": processor_rows,
                "total_amounts": _by_currency(classified),
            },
            "performance": {
                "status_stacks": status_stacks,
                "status_order": STATUS_ORDER,
                "status_table": status_table,
                "recovered_rows": recovered_rows,
                "fought_rows": fought_rows,
                "won": won, "lost": lost, "conceded": conceded, "pending": pending,
                "decided": decided,
                "win_rate": _pct(won, decided),
                "amounts_won": _by_currency(status_cases["Won"]),
                "amounts_lost": _by_currency(status_cases["Lost"]),
                "amounts_pending": _by_currency(status_cases["Decision Pending"]),
                "fought": len(fought_cases), "not_fought": len(not_fought_cases),
                # The sheet's outcome columns are simulated; say so on the page.
                "simulated": True,
            },
            "stages": stages,
            "geography": geography,
            "reason_panels": reason_panels,
            "trend": trend,
        }

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _network(case):
        raw = (case.get("payment_method") or case.get("card_network") or "").strip()
        if not raw:
            return "Other"
        low = raw.lower()
        if low.startswith("visa"):
            return "Visa"
        if low.startswith("master") or low == "mc":
            return "Mastercard"
        if "amex" in low or "american" in low:
            return "Amex"
        if low.startswith("discover"):
            return "Discover"
        if low.startswith("klarna"):
            return "Klarna"
        return raw

    @staticmethod
    def _status(case):
        """The deck's four case states.

        Prefers `case_status`, which the sheet supplies directly and which keeps
        "Not Fought" distinct. Falls back to the app's older outcome vocabulary,
        where a conceded case is recorded as a refund.
        """
        status = (case.get("case_status") or "").strip()
        if status in OUTCOME_COLORS:
            return status

        outcome = (case.get("outcome") or "").strip().lower()
        if outcome == "win":
            return "Won"
        if outcome == "lost":
            return "Lost"
        if outcome == "refunded":
            return "Not Fought"
        return "Decision Pending"

    @classmethod
    def _is_fought(cls, case):
        """Was the case defended rather than conceded?

        A recorded status settles it. Without one, fall back to the ML routing.
        """
        status = (case.get("case_status") or "").strip()
        if status in OUTCOME_COLORS:
            return status != "Not Fought"

        routing = (case.get("ml") or {}).get("routing", "")
        if routing:
            return routing != "accept_refund"
        return (case.get("outcome") or "") != "Refunded"

    @classmethod
    def _build_stages(cls, classified, total):
        """Dispute stage — the deck's "CB Type" chart.

        Stages the label map doesn't know keep their raw code, so an unexpected
        value shows up on the chart instead of vanishing into an Other bucket.
        """
        counts = Counter()
        for c in classified:
            raw = (c.get("dispute_stage") or "").strip() or "Unspecified"
            counts[STAGE_LABELS.get(raw, raw)] += 1

        ordered = [s for s in STAGE_ORDER if s in counts]
        ordered += sorted(s for s in counts if s not in STAGE_ORDER)

        peak = max(counts.values(), default=0)
        rows = [{
            "name": s,
            "count": counts[s],
            "pct": _pct(counts[s], total),
            # Bar height relative to the tallest, so a short bar stays visible.
            "bar_pct": round(counts[s] / peak * 100, 1) if peak else 0,
            "known": s in STAGE_ORDER,
        } for s in ordered]
        return {"rows": rows, "total": sum(counts.values()),
                "unmapped": [r["name"] for r in rows if not r["known"]]}

    @classmethod
    def _build_geography(cls, classified, networks, total):
        """Top delivery regions, each split by card network, plus countries.

        The region column mixes US state codes with worldwide regions and marks
        digital orders as having no destination, so this is labelled by region
        rather than by state, and digital keeps its own bar instead of being
        dropped.
        """
        region_counts = Counter()
        region_by_net = defaultdict(Counter)
        country_counts = Counter()

        for c in classified:
            src = c.get("source") or {}
            region = (src.get("DeliveryState") or "").strip() or "Unknown"
            country = (src.get("DeliveryCountry") or "").strip() or "Unknown"
            region_counts[region] += 1
            region_by_net[region][cls._network(c)] += 1
            country_counts[country] += 1

        top = [r for r, _ in region_counts.most_common(TOP_N_REGIONS)]
        # Bars are per network, so they scale against the tallest single bar —
        # scaling against the region total would squash every bar to a stub.
        peak = max((region_by_net[r][n] for r in top for n in networks), default=0)
        region_rows = [{
            "name": r,
            "count": region_counts[r],
            "pct": _pct(region_counts[r], total),
            "digital": r == DIGITAL_REGION,
            "bars": [{
                "name": n,
                "color": NETWORK_COLORS.get(n, "#c9a898"),
                "count": region_by_net[r][n],
                "bar_pct": round(region_by_net[r][n] / peak * 100, 1) if peak else 0,
            } for n in networks],
        } for r in top]

        covered = sum(region_counts[r] for r in top)
        country_peak = max(country_counts.values(), default=0)
        country_rows = [{
            "name": name,
            "count": cnt,
            "pct": _pct(cnt, total),
            "bar_pct": round(cnt / country_peak * 100, 1) if country_peak else 0,
        } for name, cnt in country_counts.most_common()]

        return {
            "region_rows": region_rows,
            "country_rows": country_rows,
            "covered": covered,
            "other": total - covered,
            "distinct_regions": len(region_counts),
            "peak": peak,
        }

    @classmethod
    def _build_reason_panels(cls, net_cases, networks):
        """Top reason codes within each card network.

        Each panel reports its own case count: on a sheet where reason codes are
        nearly unique, the bars are short because the sample is small, and the
        reader should be able to see that rather than guess.
        """
        panels = []
        for n in networks:
            cases = net_cases.get(n, [])
            counts = Counter()
            labels = {}
            for c in cases:
                code = (c.get("reason_code") or "").strip() or "Unspecified"
                counts[code] += 1
                labels.setdefault(code, (c.get("source") or {}).get("ReasonMsg", "")
                                  or c.get("reason_description", "") or code)
            top = counts.most_common(TOP_N_REASONS)
            peak = top[0][1] if top else 0
            panels.append({
                "network": n,
                "color": NETWORK_COLORS.get(n, "#c9a898"),
                "cases": len(cases),
                "distinct": len(counts),
                "covered": sum(cnt for _, cnt in top),
                "rows": [{
                    "code": code,
                    "label": labels[code],
                    "count": cnt,
                    "pct": _pct(cnt, len(cases)),
                    "bar_pct": round(cnt / peak * 100, 1) if peak else 0,
                } for code, cnt in top],
            })
        return panels

    @staticmethod
    def _add_arcs(rows):
        """Attach cumulative start/end degrees for a CSS conic-gradient pie."""
        total = sum(r["count"] for r in rows)
        cursor = 0.0
        for r in rows:
            span = (r["count"] / total * 360) if total else 0
            r["start_deg"] = round(cursor, 2)
            cursor += span
            r["end_deg"] = round(cursor, 2)
        if rows:
            rows[-1]["end_deg"] = 360

    @classmethod
    def _case_date(cls, case):
        for field in ("dispute_creation_date", "submission_date", "transaction_date"):
            dt = parse_any_datetime(case.get(field))
            if dt:
                return dt
        return None

    @classmethod
    def _build_trend(cls, classified, networks):
        """Bucket cases by day / week / month, stacked by card network."""
        buckets = {"daily": defaultdict(Counter), "weekly": defaultdict(Counter),
                   "monthly": defaultdict(Counter)}
        amounts = {"daily": defaultdict(float), "weekly": defaultdict(float),
                   "monthly": defaultdict(float)}
        undated = 0

        for c in classified:
            dt = cls._case_date(c)
            if not dt:
                undated += 1
                continue
            net = cls._network(c)
            amt = safe_float(c.get("amount"))
            keys = {
                "daily": dt.strftime("%Y-%m-%d"),
                "weekly": (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d"),
                "monthly": dt.strftime("%Y-%m"),
            }
            for gran, key in keys.items():
                buckets[gran][key][net] += 1
                amounts[gran][key] += amt

        series = {}
        for gran in ("daily", "weekly", "monthly"):
            points = []
            for key in sorted(buckets[gran]):
                counts = buckets[gran][key]
                points.append({
                    "key": key,
                    "label": cls._trend_label(gran, key),
                    "total": sum(counts.values()),
                    "amount": round(amounts[gran][key], 2),
                    "by_network": {n: counts[n] for n in networks},
                })
            series[gran] = points

        peak = max((p["total"] for p in series["daily"]), default=0)
        return {
            "series": series,
            "undated": undated,
            "peak_daily": peak,
            "span": {
                "start": series["daily"][0]["key"] if series["daily"] else "",
                "end": series["daily"][-1]["key"] if series["daily"] else "",
                "days": len(series["daily"]),
            },
        }

    @staticmethod
    def _trend_label(gran, key):
        dt = parse_any_datetime(key if gran != "monthly" else key + "-01")
        if not dt:
            return key
        if gran == "monthly":
            return dt.strftime("%b %Y")
        if gran == "weekly":
            return "w/c " + dt.strftime("%d %b")
        return dt.strftime("%d %b")
