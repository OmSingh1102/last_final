from collections import Counter

from chargeback.analytics.agent_desk import AgentDesk
from chargeback.analytics.team_console import TeamConsole
from chargeback.utils.hashing import deterministic_seed


class ManagerConsole:
    """The management view: everything, plus the controls only a manager gets.

    This is aggregation rather than new analysis — DashboardAnalytics and
    ManagerCharts already compute the numbers, so the job here is to answer the
    questions the management hub asks that nothing else does: how much of the
    book arrived automatically versus by hand, what work is still outstanding,
    and who owns which client and which agents.
    """

    @staticmethod
    def _sources(cases):
        """Holistic count across both ingest paths.

        Cases loaded before source tagging existed have no marker; they came
        from the startup sheet, so they count as automated rather than being
        dropped from the total.
        """
        counts = Counter((c.get("ingest_source") or "automated") for c in cases)
        automated = counts.get("automated", 0)
        manual = counts.get("manual", 0)
        total = automated + manual
        return {
            "total": total,
            "automated": automated,
            "manual": manual,
            "automated_pct": round(automated / total * 100) if total else 0,
            "manual_pct": round(manual / total * 100) if total else 0,
        }

    @classmethod
    def _history(cls, all_cases, cases_by_id, mapping):
        """Every case, with the fields the history view filters on."""
        rows = []
        for c in all_cases:
            case = cases_by_id.get(c["case_id"], {})
            rows.append({
                **c,
                "bucket": TeamConsole._bucket_of(case, mapping),
                "ingest_source": case.get("ingest_source") or "automated",
                # Same resolver the rest of the app uses: a lead's allocation
                # wins, otherwise the hash. DashboardAnalytics only names an
                # agent on HITL rows, so reading that would under-count by more
                # than half and leave the lead totals not summing to the book.
                "assigned_agent": (
                    case.get("assigned_agent")
                    or AgentDesk.AGENTS[deterministic_seed(c["case_id"])
                                        % len(AgentDesk.AGENTS)]),
                "customer": (case.get("source", {}) or {}).get("UserFullName", ""),
                "dispute_date": (case.get("dispute_creation_date", "") or "")[:10],
                "outstanding": c.get("outcome") == "Pending",
            })
        return rows

    @classmethod
    def compute(cls, cases, analytics, charts, lead_agents, client_routing,
                team_leads):
        cases_by_id = {c["case_id"]: c for c in cases}
        mapping = TeamConsole._bucket_map(cases)
        history = cls._history(analytics.get("all_cases", []), cases_by_id, mapping)
        outstanding = [r for r in history if r["outstanding"]]

        # ── Which lead owns which agents and which client books ──
        leads = []
        for lead in team_leads:
            agents = lead_agents.get(lead, [])
            clients = [b for b, owner in client_routing.items() if owner == lead]
            owned = [r for r in history if r["assigned_agent"] in agents]
            leads.append({
                "name": lead,
                "agents": agents,
                "clients": clients,
                "cases": len(owned),
                "outstanding": sum(1 for r in owned if r["outstanding"]),
            })

        unassigned = [a for a in TeamConsole.BUCKET_LABELS
                      if a not in client_routing]

        return {
            "sources": cls._sources(cases),
            "history": history,
            "outstanding": outstanding,
            "leads": leads,
            "team_leads": team_leads,
            "client_routing": dict(client_routing),
            "lead_agents": {k: list(v) for k, v in lead_agents.items()},
            "unrouted_clients": unassigned,
            "bucket_labels": TeamConsole.BUCKET_LABELS,
            "filters": {
                "buckets": TeamConsole.BUCKET_LABELS,
                "agents": sorted({r["assigned_agent"] for r in history
                                  if r["assigned_agent"]}),
                "handlings": sorted({r["handling"] for r in history
                                     if r.get("handling")}),
                "outcomes": sorted({r["outcome"] for r in history
                                    if r.get("outcome")}),
                "processors": sorted({r["processor"] for r in history
                                      if r.get("processor")}),
                "networks": sorted({r["network"] for r in history
                                    if r.get("network")}),
                "submissions": sorted({r["submission_status"] for r in history
                                       if r.get("submission_status")}),
                "sources": ["automated", "manual"],
            },
            # Handling is one field with mutually exclusive values, so these do
            # partition the book — unlike outstanding/submitted, which are two
            # independent flags and overlap. Counting it here lets the page show
            # a strip of chips that add up to the total.
            "handling_counts": dict(Counter(r["handling"] for r in history
                                            if r.get("handling"))),
            "totals": {
                "cases": len(history),
                "outstanding": len(outstanding),
                "submitted": sum(1 for r in history
                                 if r.get("submission_status") == "Submitted"),
                "avg_resolution": analytics.get("kpis", {}).get("avg_resolution_days", 0),
                "networks": len(charts.get("networks", [])),
                "processors": len(analytics.get("processor_perf", {})),
            },
        }
