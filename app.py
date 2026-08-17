from flask import (Flask, render_template, jsonify, request, redirect, url_for,
                   flash, session, send_from_directory, abort)
from werkzeug.utils import secure_filename
import hashlib
import hmac
import json
import os
import re
import csv as _csv
import io
from collections import Counter, defaultdict
from datetime import datetime
from functools import wraps

# ─── Modular imports ───────────────────────────────────────────────────────────
from chargeback.utils.datetime_helpers import safe_float as _safe_float, parse_any_datetime as _parse_any_datetime, fmt_datetime as _fmt_datetime
from chargeback.utils.hashing import deterministic_seed
from chargeback.data.loader import ChargebackCaseLoader
from chargeback.data.seed import CASES, IngestionDemo
from chargeback.engines.reason_code import REASON_CODES, SCENARIO_CATEGORIES, ReasonCodeInterpreter, ReasonCodeRulebook
from chargeback.engines.pipeline import DatabaseOrchestrator, DecisionPackageBuilder, ChargebackPipeline
from chargeback.engines.ai_validation import AIValidationEngine, ChargebackClassifier
from chargeback.engines.evidence_collection import EvidenceCollectionEngine
from chargeback.engines.cover_letter import RepositoryEngine, CoverLetterAIEngine, COVER_LETTER_BODIES
from chargeback.engines.evidence_documents import DOCUMENTS
from chargeback.engines.pdf_converter import PDFPacketConverter
from chargeback.engines.dispute_platform import DisputeAutomationPlatform, PSPDisputeAPI, GatewayEvidenceAPI, CRMOrderAPI, PODTrackingAPI
from chargeback.analytics.dashboard import DashboardAnalytics
from chargeback.analytics.manager_charts import ManagerCharts
from chargeback.analytics.executive import ExecutiveAnalytics
from chargeback.analytics.qa_review import QAReviewEngine
from chargeback.analytics.agent_desk import AgentDesk
from chargeback.analytics.agent_console import AgentConsole
from chargeback.analytics.team_console import TeamConsole
from chargeback.analytics.manager_console import ManagerConsole
from chargeback.analytics.ingest_console import IngestConsole
from chargeback.analytics.client_console import ClientConsole
from chargeback.adapters.registry import register_default_adapters

# ─── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "chargeback-dev-key"
register_default_adapters()


# ─── Sign-in and role routing ─────────────────────────────────────────────────
# Demo gate only: a hardcoded user table, no password hashing, and the session
# cookie is signed with the dev secret above. It decides which profile page a
# user lands on — it is not a security control.
#
# The login form deliberately carries no role selector. The role is derived from
# the credentials on the server and the user is redirected accordingly, so which
# profile someone holds is never something they pick for themselves.
_DEMO_USERS = {
    # One login per agent, so each signs in to their own queue. All three hold
    # the same role — the queue they own is what differs, via AGENT_LOGIN_MAP.
    "agent":   {"password": "agent123",   "role": "agent"},
    "agent2":  {"password": "agent2123",  "role": "agent"},
    "agent3":  {"password": "agent3123",  "role": "agent"},
    # Two team leads, so the manager's client-to-lead routing has somewhere to
    # route to. Which agents report to which lead lives in LEAD_AGENTS.
    "admin":   {"password": "admin123",   "role": "admin"},
    "admin2":  {"password": "admin2123",  "role": "admin"},
    "manager": {"password": "manager123", "role": "manager"},
    # One login per client book. A merchant signs in to their own brand and
    # sees nothing else — which book a login owns lives in CLIENT_LOGIN_MAP.
    "acme":      {"password": "acme123",      "role": "client"},
    "northwind": {"password": "northwind123", "role": "client"},
}
STRAIVE_USERS = {name: dict(rec) for name, rec in _DEMO_USERS.items()}

# Optional overrides: AGENT_PASSWORD / AGENT2_PASSWORD / ADMIN_PASSWORD / …
_ENV_OVERRIDDEN = False
for _name in STRAIVE_USERS:
    _env_pw = os.environ.get(f"{_name.upper()}_PASSWORD")
    if _env_pw:
        STRAIVE_USERS[_name]["password"] = _env_pw
        _ENV_OVERRIDDEN = True

# Only advertise credentials on the login card while they are the built-in demo
# set — never print a password someone set through the environment.
_SHOW_DEMO_HINT = not _ENV_OVERRIDDEN

# Which of the hash-assigned queues (Agent1/2/3) a login owns. Cases are split
# by deterministic_seed(case_id) % 3, so this one mapping is all that is needed
# to scope every agent page to a single person's work.
AGENT_LOGIN_MAP = {"agent": "Agent1", "agent2": "Agent2", "agent3": "Agent3"}
DEFAULT_AGENT = "Agent1"

# The client-side mirror. Deliberately no DEFAULT_CLIENT: an agent login with no
# queue can fall back to Agent1 because a manager inspecting a queue is
# legitimate, but nobody may fall back into a brand's book. An unmapped client
# login sees nothing at all.
CLIENT_LOGIN_MAP = {"acme": "Acme Online Store", "northwind": "Northwind Traders"}

# The team hierarchy the manager owns: which agents report to which lead, and
# which client book each lead is responsible for. Both are editable from the
# management console and persisted beside the other override stores.
LEAD_AGENTS = {"admin": ["Agent1", "Agent2"], "admin2": ["Agent3"]}
CLIENT_ROUTING = {"Acme Online Store": "admin", "Northwind Traders": "admin2"}
TEAM_LEADS = ["admin", "admin2"]

# What we know about each client book as a customer rather than as a workload:
# the service tier their contract puts them on, and the merchant account record
# behind it. Keyed by the same labels the routing above uses, so a client has
# one identity across the manager console, the rebuttal builder and the portal.
#
# The tier is not a label. It decides how a representment packet is built —
# see ClientConsole.section_plan and counter_evidence().
CLIENT_PROFILES = {
    label: {"tier": ClientConsole.DEFAULT_TIER,
            "account": {key: "" for key in ClientConsole.ACCOUNT_FIELDS}}
    for label in TeamConsole.BUCKET_LABELS
}
CLIENT_PROFILES["Acme Online Store"].update({
    "tier": "fully_automated",
    "account": {"corp_name": "Acme Commerce Inc.", "signer_name": "R. Halloran",
                "processor_name": "Cybersource", "dba_name": "Acme Online Store",
                "mid_no": "8412200917", "approved_mv": "$75,000",
                "descriptor": "acme-store.example.com",
                "processor_id": "CYB-4471", "status": "Active",
                "pending_with": "", "updates": ""},
})
CLIENT_PROFILES["Northwind Traders"].update({
    "tier": "manual",
    "account": {"corp_name": "Northwind Trading Co.", "signer_name": "M. Devereux",
                "processor_name": "Humboldt", "dba_name": "Northwind Traders",
                "mid_no": "", "approved_mv": "", "descriptor": "",
                "processor_id": "", "status": "WIP",
                "pending_with": "Client",
                "updates": "Applications sent for signature"},
})

# Bumped every time shared state changes — routing, allocations, rework
# releases, agent actions, ingestion. Every page stamps the value it rendered
# with; the browser polls /state/version and offers a refresh when the two
# diverge. That is what stops one user's screen sitting stale after another
# user changes something underneath it.
#
# A plain int is enough: CPython guarantees `+= 1` under the GIL is not going
# to lose an increment badly enough to matter here, and the only thing anyone
# does with the number is compare it for equality.
STATE_VERSION = 0


def _bump_state():
    """Mark shared state as changed so open pages know to refresh."""
    global STATE_VERSION
    STATE_VERSION += 1

# Where each role lands after signing in, and what the header chip calls them.
ROLE_HOME = {"agent": "agent_dashboard", "admin": "admin_dashboard",
             "manager": "manager_hub", "client": "client_dashboard"}
ROLE_LABEL = {"agent": "Agent", "admin": "Admin (Team Lead)",
              "manager": "Management", "client": "Client"}

# Which roles may open each profile page. Anything not listed is shared by every
# signed-in user — case detail, counter evidence, rebuttal and so on.
PAGE_ROLES = {
    "manager_hub":      {"manager"},
    "agent_desk":       {"admin", "manager"},
    # QA Review is an audit tool over everyone's work, so it is not an agent
    # page any more — agents get the console below instead.
    "qa_review":        {"manager"},
    "agent_dashboard":  {"agent", "manager"},
    "agent_chargebacks": {"agent", "manager"},
    "agent_repository": {"agent", "manager"},
    "agent_settings":   {"agent", "manager"},
    "admin_dashboard":  {"admin", "manager"},
    "admin_allocation": {"admin", "manager"},
    "admin_approvals":  {"admin", "manager"},
    "admin_repository": {"admin", "manager"},
    # Management only — onboarding and client-to-lead routing are meant to be
    # invisible to team leads and agents, not merely unlinked.
    "manager_history":  {"manager"},
    "manager_onboarding": {"manager"},
    "manager_settings": {"manager"},
    # The client portal. A merchant sees their own book and nothing else.
    "client_dashboard":   {"client"},
    "client_chargebacks": {"client"},
    "client_case":        {"client"},
}

# The side pane on the management console. Data Ingestion appears here and on
# the team-lead console, but never on an agent's.
MANAGER_NAV = [
    {"endpoint": "manager_hub",        "label": "Data Dashboard",      "icon": "&#128202;"},
    {"endpoint": "manager_history",    "label": "Chargeback History",  "icon": "&#128220;"},
    {"endpoint": "manager_onboarding", "label": "Onboarding & Clients", "icon": "&#127970;"},
    {"endpoint": "manager_settings",   "label": "System Settings",     "icon": "&#9881;"},
    {"endpoint": "ingest",             "label": "Data Ingestion",      "icon": "&#128229;"},
]

# The sidebar on the team-lead console, in display order. Data Ingestion points
# at the existing /ingest screen — manually uploading a case dump when the
# portal API fails is exactly a team lead's job.
ADMIN_NAV = [
    {"endpoint": "admin_dashboard",   "label": "Dashboard",        "icon": "&#128202;"},
    {"endpoint": "admin_allocation",  "label": "Case Allocation",  "icon": "&#128228;"},
    {"endpoint": "admin_approvals",   "label": "Rework Approvals", "icon": "&#128275;"},
    {"endpoint": "agent_desk",        "label": "Team Queue",       "icon": "&#128187;"},
    {"endpoint": "admin_repository",  "label": "Repository",       "icon": "&#128193;"},
    {"endpoint": "ingest",            "label": "Data Ingestion",   "icon": "&#128229;"},
]

# The sidebar on the agent console, in display order.
AGENT_NAV = [
    {"endpoint": "agent_dashboard",   "label": "Dashboard",             "icon": "&#128202;"},
    {"endpoint": "agent_chargebacks", "label": "Chargeback Management", "icon": "&#128179;"},
    {"endpoint": "agent_repository",  "label": "Repository",            "icon": "&#128193;"},
    {"endpoint": "agent_settings",    "label": "Settings",              "icon": "&#9881;"},
]

# The sidebar on the client portal. Kept out of NAV_PAGES deliberately — the
# top nav subscripts PAGE_ROLES directly, and the per-role sidebars do not.
CLIENT_NAV = [
    {"endpoint": "client_dashboard",   "label": "Dashboard",      "icon": "&#128202;"},
    {"endpoint": "client_chargebacks", "label": "My Chargebacks", "icon": "&#128179;"},
]

# The top nav, in display order. Filtered per role by _inject_nav below, so the
# bar can never offer a page the signed-in user would be bounced off.
NAV_PAGES = [
    {"endpoint": "manager_hub",     "label": "Manager Hub", "icon": "&#128202;"},
    {"endpoint": "agent_desk",      "label": "Admin Page",  "icon": "&#128187;"},
    {"endpoint": "agent_dashboard", "label": "Agent Page",  "icon": "&#128293;"},
]

# Reachable without signing in. Everything else is gated by _require_sign_in.
PUBLIC_ENDPOINTS = {"portal", "login", "client_login", "logout", "static"}

# Everything a signed-in client may reach. state_version keeps the live-refresh
# poll working; counter_evidence is deliberately absent — it carries upload and
# delete controls that belong to staff.
CLIENT_ENDPOINTS = {"client_dashboard", "client_chargebacks", "client_case",
                    "state_version", "logout", "static"}


def _staff_logins():
    return [(n, r) for n, r in _DEMO_USERS.items() if r["role"] != "client"]


def _client_logins():
    return [(n, r) for n, r in _DEMO_USERS.items() if r["role"] == "client"]


def _login_page(error=None, username="", next_url="", status=200):
    return render_template(
        "login.html", next=next_url, error=error, username=username,
        # Staff door: only staff accounts. Listing the client logins here would
        # advertise a merchant's credentials to everyone who opens it.
        demo_users=_staff_logins() if _SHOW_DEMO_HINT else None,
    ), status


def _client_login_page(error=None, username="", status=200):
    return render_template(
        "client_login.html", error=error, username=username,
        demo_users=_client_logins() if _SHOW_DEMO_HINT else None,
    ), status


def _authenticate(username: str, password: str):
    """Return (username, role) for valid credentials, else (None, None).

    Compares bytes, not str: compare_digest rejects non-ASCII strings, and a
    typed accent would otherwise raise instead of simply failing the check.
    Every known user is checked even after a match, and `&` is used rather than
    `and`, so the time taken does not depend on which name was typed.
    """
    typed_user = (username or "").encode("utf-8")
    typed_pw = (password or "").encode("utf-8")
    matched = (None, None)
    for name, rec in STRAIVE_USERS.items():
        if (hmac.compare_digest(typed_user, name.encode("utf-8"))
                & hmac.compare_digest(typed_pw, rec["password"].encode("utf-8"))):
            matched = (name, rec["role"])
    return matched


def _role_home_url(role=None):
    """The page this role owns; the portal if there is no usable role."""
    endpoint = ROLE_HOME.get(role or session.get("role"))
    return url_for(endpoint) if endpoint else url_for("portal")


def _safe_next(target: str) -> str:
    """Only follow same-site paths.

    Rejects absolute URLs and protocol-relative ones. Browsers normalise
    backslashes to slashes, so '/\\host' is as dangerous as '//host'.
    """
    if target and target.startswith("/") and target[1:2] not in ("/", "\\"):
        return target
    return _role_home_url()


def role_required(*roles):
    """Restrict a view to the given roles.

    A signed-out visitor goes to the login form with their destination
    remembered. A signed-in user holding the wrong role is sent to their own
    page rather than shown an error — the nav never offers them the link, so
    reaching here means a hand-typed URL.
    """
    allowed = set(roles)

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            role = session.get("role")
            if not role:
                return redirect(url_for("login", next=request.path))
            if role not in allowed:
                return redirect(_role_home_url(role))
            return view(*args, **kwargs)
        return wrapper
    return decorator


# Kept so the existing decorator site reads the same as before.
manager_required = role_required("manager")


@app.before_request
def _require_sign_in():
    """Everything outside PUBLIC_ENDPOINTS needs a session.

    One hook rather than a decorator on each of the thirty-odd routes: easier to
    audit, and impossible to forget when a route is added. `endpoint is None`
    means the URL matched nothing — let it fall through to the 404.
    """
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not session.get("role"):
        return redirect(url_for("portal"))
    # A client is a customer, not staff. Allow-listing here rather than
    # decorating thirty routes means anything added later is closed to them
    # until it is named — the safe direction to fail in.
    if (session.get("role") == "client"
            and request.endpoint not in CLIENT_ENDPOINTS):
        return redirect(_role_home_url("client"))
    return None


@app.context_processor
def _inject_nav():
    """Give every template the nav its role is allowed to see."""
    role = session.get("role")
    return {
        # .get, not [] — a NAV_PAGES entry with no PAGE_ROLES key used to raise
        # KeyError on every page render for every role.
        "nav_pages": [p for p in NAV_PAGES
                      if role in PAGE_ROLES.get(p["endpoint"], set())],
        "agent_nav": AGENT_NAV,
        "admin_nav": ADMIN_NAV,
        "manager_nav": MANAGER_NAV,
        "client_nav": CLIENT_NAV,
        "role_label": ROLE_LABEL.get(role, ""),
        # Stamped into every page so the browser can tell when the server has
        # moved on without it.
        "state_version": STATE_VERSION,
    }


@app.route("/state/version")
def state_version():
    """Cheap staleness probe polled by every open console.

    Deliberately does no analytics — it reads one integer, so polling it every
    few seconds costs nothing even with every role signed in at once.
    """
    return jsonify({"v": STATE_VERSION})


@app.route("/")
def portal():
    """Front door: choose Straive or Client sign-in."""
    if session.get("role"):
        return redirect(_role_home_url())
    return render_template("portal.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Straive sign-in — one form for all three profiles."""
    next_url = request.values.get("next", "")
    if request.method == "POST":
        name, role = _authenticate(request.form.get("username", ""),
                                   request.form.get("password", ""))
        if role:
            session["role"] = role
            session["user"] = name
            # Still set for anything that reads the old flag.
            session["is_manager"] = (role == "manager")
            return redirect(_safe_next(next_url) if next_url
                            else _role_home_url(role))
        # flash() is never rendered anywhere in this app, so surface it inline.
        return _login_page(error="Incorrect username or password.",
                           username=request.form.get("username", ""),
                           next_url=next_url, status=401)
    if session.get("role"):
        return redirect(_safe_next(next_url) if next_url else _role_home_url())
    return _login_page(next_url=next_url)


@app.route("/login/client", methods=["GET", "POST"])
def client_login():
    """Client sign-in — a merchant lands on their own brand's book."""
    if request.method == "POST":
        name, role = _authenticate(request.form.get("username", ""),
                                   request.form.get("password", ""))
        # _authenticate accepts any valid credential, so the role is checked
        # here: a Straive login must not be usable through the client door.
        # Same 401 either way — do not confirm which half was wrong.
        if role == "client":
            session["role"] = role
            session["user"] = name
            session["is_manager"] = False
            return redirect(_role_home_url(role))
        return _client_login_page(error="Incorrect username or password.",
                                  username=request.form.get("username", ""),
                                  status=401)
    if session.get("role") == "client":
        return redirect(_role_home_url())
    return _client_login_page()


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("portal"))

# Working data set loaded at boot (see _load_startup_cases below).
STARTUP_DATASET = "Chargeback_case_dummy_data_100.csv"

# Actions an agent can record against a case from the Agent Desk, and what each
# one means for the fields the rest of the app reads.
#
# Manager Hub, the dashboard and QA Review all read `case_status` / `outcome`,
# never `agent_action` — so an action has to rewrite those to be visible
# anywhere else. An agent's decision overrides whatever the sheet recorded.
#
# "Contested" stays Decision Pending on purpose: defending a case is not the
# same as winning it. It still moves the fought/not-fought split, because
# `_is_fought()` keys off `case_status != "Not Fought"`.
AGENT_ACTION_EFFECTS = {
    "Contested":       {"case_status": "Decision Pending", "outcome": "Pending",
                        "submission_status": "Submitted"},
    "Not Fought":      {"case_status": "Not Fought", "outcome": "Refunded",
                        "submission_status": "Not Submitted"},
    "Waiting for POD": {"case_status": "Decision Pending", "outcome": "Pending",
                        "submission_status": "Awaiting Evidence"},
    "Pending":         {"case_status": "Decision Pending", "outcome": "Pending",
                        "submission_status": "Pending"},
}
AGENT_ACTIONS = list(AGENT_ACTION_EFFECTS)

# Agent decisions outlive the process here. Kept out of static/, which is served
# to the browser.
AGENT_ACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "agent_actions.json")


def _apply_agent_action(case, action, at=None):
    """Record an agent's decision and push it onto the reporting fields.

    One code path for both a live action and a restore at boot, so the two
    cannot drift.
    """
    case["agent_action"] = action
    case["agent_action_at"] = at or datetime.now().strftime("%b %d, %Y, %H:%M:%S")
    case.update(AGENT_ACTION_EFFECTS[action])
    return case["agent_action_at"]


def _save_agent_actions():
    """Persist every recorded decision, keyed by case id."""
    _bump_state()
    stored = {c["case_id"]: {"action": c["agent_action"], "at": c.get("agent_action_at", "")}
              for c in CASES if c.get("agent_action")}
    try:
        with open(AGENT_ACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
    except OSError as exc:
        app.logger.warning("Could not save agent actions: %s", exc)


def _restore_agent_actions():
    """Re-apply stored decisions onto the current case set.

    A missing or unreadable file just means nothing has been actioned yet — it
    must never stop the app booting. Unknown actions and case ids that are not
    in the current sheet are skipped rather than trusted.
    """
    try:
        with open(AGENT_ACTIONS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(stored, dict):
        return 0

    by_id = {c["case_id"]: c for c in CASES}
    restored = 0
    for case_id, entry in stored.items():
        case = by_id.get(case_id)
        action = (entry or {}).get("action") if isinstance(entry, dict) else None
        if case is None or action not in AGENT_ACTION_EFFECTS:
            continue
        _apply_agent_action(case, action, entry.get("at"))
        restored += 1
    return restored


# ─── Team-lead overrides: who owns a case, and what may be reworked ────────────
# Both follow the agent_actions.json pattern above: a JSON file at the project
# root (never under static/, which is web-served), restored inside _apply_cases
# so an override survives a restart and a sheet re-upload alike.
ALLOCATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "case_allocations.json")
REWORK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "rework_releases.json")


def _save_allocations():
    _bump_state()
    stored = {c["case_id"]: c["assigned_agent"]
              for c in CASES if c.get("assigned_agent")}
    try:
        with open(ALLOCATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
    except OSError as exc:
        app.logger.warning("Could not save allocations: %s", exc)


def _restore_allocations():
    """Re-apply a team lead's re-assignments.

    Sets `assigned_agent`, which AgentDesk prefers over its hash. An unreadable
    file, an unknown case id or an agent name that is not on the roster are all
    skipped rather than trusted.
    """
    try:
        with open(ALLOCATIONS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(stored, dict):
        return 0

    by_id = {c["case_id"]: c for c in CASES}
    restored = 0
    for case_id, agent in stored.items():
        case = by_id.get(case_id)
        if case is None or agent not in AgentDesk.AGENTS:
            continue
        case["assigned_agent"] = agent
        restored += 1
    return restored


def _save_rework_releases():
    _bump_state()
    stored = {c["case_id"]: c["rework_released"]
              for c in CASES if c.get("rework_released")}
    try:
        with open(REWORK_FILE, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
    except OSError as exc:
        app.logger.warning("Could not save rework releases: %s", exc)


ROUTING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "client_routing.json")


def _save_routing():
    """Persist the team hierarchy the manager controls."""
    _bump_state()
    try:
        with open(ROUTING_FILE, "w", encoding="utf-8") as f:
            json.dump({"lead_agents": LEAD_AGENTS,
                       "client_routing": CLIENT_ROUTING}, f, indent=2)
    except OSError as exc:
        app.logger.warning("Could not save routing: %s", exc)


def _restore_routing():
    """Re-apply the manager's agent and client assignments.

    Entries naming a lead or agent that no longer exists are skipped, so an
    edited file can never strand a queue with an owner nobody can sign in as.
    """
    try:
        with open(ROUTING_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(stored, dict):
        return 0

    restored = 0
    leads = stored.get("lead_agents")
    if isinstance(leads, dict):
        cleaned = {lead: [a for a in agents if a in AgentDesk.AGENTS]
                   for lead, agents in leads.items()
                   if lead in TEAM_LEADS and isinstance(agents, list)}
        if cleaned:
            LEAD_AGENTS.clear()
            LEAD_AGENTS.update(cleaned)
            restored += 1

    routing = stored.get("client_routing")
    if isinstance(routing, dict):
        cleaned = {bucket: lead for bucket, lead in routing.items()
                   if lead in TEAM_LEADS}
        if cleaned:
            CLIENT_ROUTING.clear()
            CLIENT_ROUTING.update(cleaned)
            restored += 1
    return restored


CLIENT_PROFILES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "client_profiles.json")


def _save_client_profiles():
    """Persist each client's service tier and merchant account record."""
    _bump_state()
    try:
        with open(CLIENT_PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(CLIENT_PROFILES, f, indent=2)
    except OSError as exc:
        app.logger.warning("Could not save client profiles: %s", exc)


def _restore_client_profiles():
    """Re-apply the tier and account record the manager has set per client.

    Updates in place rather than swapping the dict wholesale: the key set is
    the client roster, so a stored file that has lost a client must not be able
    to delete that client's defaults. A tier the rebuttal builder does not
    implement is skipped, which means a hand-edited file can never put a client
    into a mode with no code behind it.
    """
    try:
        with open(CLIENT_PROFILES_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(stored, dict):
        return 0

    restored = 0
    for client, entry in stored.items():
        if client not in CLIENT_PROFILES or not isinstance(entry, dict):
            continue
        if entry.get("tier") in ClientConsole.TIERS:
            CLIENT_PROFILES[client]["tier"] = entry["tier"]
        account = entry.get("account")
        if isinstance(account, dict):
            CLIENT_PROFILES[client]["account"].update(
                {k: str(v) for k, v in account.items()
                 if k in ClientConsole.ACCOUNT_FIELDS
                 and isinstance(v, (str, int, float))})
        restored += 1
    return restored


def _restore_rework_releases():
    """Re-apply locks a team lead has released for rework."""
    try:
        with open(REWORK_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(stored, dict):
        return 0

    by_id = {c["case_id"]: c for c in CASES}
    restored = 0
    for case_id, entry in stored.items():
        case = by_id.get(case_id)
        if case is None or not isinstance(entry, dict):
            continue
        case["rework_released"] = entry
        restored += 1
    return restored

# ─── Pre-computed Evidence Collection ──────────────────────────────────────────
_classified_for_evidence = AIValidationEngine.classify_all(CASES)
EVIDENCE_RESULTS = EvidenceCollectionEngine.collect_all(_classified_for_evidence)
EVIDENCE_STATS = EvidenceCollectionEngine.get_aggregate_stats(EVIDENCE_RESULTS)


def _get_reason(case):
    """Get reason code info with safe defaults for unknown codes."""
    rc = case.get("reason_code", "")
    reason = REASON_CODES.get(rc, {})
    if not reason:
        desc = case.get("reason_description", "") or case.get("scenario", "")
        reason = {
            "title": desc or f"Reason Code {rc}",
            "definition": desc,
            "network_codes": {"Visa": rc, "Mastercard": rc, "Amex": rc, "Discover": rc},
            "scenarios": [desc] if desc else [],
            "merchant_challenge": "",
            "defense_goals": [],
            "supporting_docs_general": [],
            "supporting_docs_platform": [],
            "portals": [],
        }
    return reason


# ─── Merchant Configuration ────────────────────────────────────────────────────
# Placeholder identity for a generic install. Everything here is editable at
# /merchant-config, and the case builders read these values rather than carrying
# their own copies, so changing it once re-brands the whole app.
MERCHANT_CONFIG = {
    "customer_id": "CUST-001",
    "company_name": "Acme Commerce Inc.",
    "dba_name": "Acme Online Store",
    "descriptor_url": "acme-store.example.com",
    "services": "Chargeback",
    "merchant_account_number": "",
    "mid_alias_name": "",
    "status": "Active",
    "notes": "",
    "gateway_name": "Cybersource",
    "gateway_url": "",
    "gateway_username": "",
    "gateway_password": "",
    "gateway_api_login_id": "",
    "gateway_transaction_key": "",
    "processor_name": "American Express",
    "processor_url": "",
    "processor_username": "",
    "processor_password": "",
    "crm_name": "Konnektive",
    "crm_url": "",
    "crm_username": "",
    "crm_password": "",
    "crm_api_username": "",
    "crm_api_password": "",
}

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/merchant-config", methods=["GET", "POST"])
def merchant_config():
    if request.method == "POST":
        for key in MERCHANT_CONFIG:
            val = request.form.get(key, "")
            if val:
                MERCHANT_CONFIG[key] = val
        return redirect(url_for("merchant_config"))
    return render_template("merchant_config.html", config=MERCHANT_CONFIG)


@app.route("/api-integration")
def api_integration():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reason_codes = sorted(set(c.get("reason_code", "") for c in CASES)) if CASES else list(REASON_CODES.keys())
    return render_template("api_integration.html", now=now, total_cases=len(CASES), reason_codes=reason_codes)


# Moved off "/" so the portal can own the front door. The endpoint name is
# unchanged, so every existing url_for('dashboard') still resolves.
@app.route("/dashboard")
def dashboard():
    scenarios = sorted(set(c["scenario"] for c in CASES))
    processors = sorted(set(c["processor"] for c in CASES))
    outcomes = sorted(set(c["outcome"] for c in CASES))
    classified = AIValidationEngine.classify_all(CASES)
    return render_template("dashboard.html", cases=classified, scenarios=scenarios,
                           processors=processors, outcomes=outcomes)


@app.route("/ai-overview")
def ai_overview():
    stats = AIValidationEngine.get_pipeline_stats(CASES)
    return render_template("ai_overview.html", stats=stats, reason_codes=REASON_CODES)


@app.route("/evidence-collection")
def evidence_collection():
    classified = AIValidationEngine.classify_all(CASES)
    return render_template("evidence_collection.html",
                           cases=classified,
                           evidence=EVIDENCE_RESULTS,
                           stats=EVIDENCE_STATS,
                           apis=EvidenceCollectionEngine.APIS)


def _manager_context():
    """The three analytics bundles every management page renders from."""
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    analytics = DashboardAnalytics.compute(
        CASES, ml_stats, EVIDENCE_STATS, EVIDENCE_RESULTS, REASON_CODES)
    charts = ManagerCharts.compute(
        ml_stats["classified_cases"], ChargebackCaseLoader.load_orders())
    console = ManagerConsole.compute(CASES, analytics, charts, LEAD_AGENTS,
                                     CLIENT_ROUTING, TEAM_LEADS)
    return {"a": analytics, "ml": ml_stats, "ev": EVIDENCE_STATS,
            "ch": charts, "m": console}


@app.route("/manager-hub")
@manager_required
def manager_hub():
    return render_template("manager_hub.html", **_manager_context())


@app.route("/manager/history")
@role_required("manager")
def manager_history():
    return render_template("manager_history.html", **_manager_context())


@app.route("/manager/onboarding")
@role_required("manager")
def manager_onboarding():
    # Passed to this page only — _manager_context feeds four pages and none of
    # the others render a client profile.
    return render_template("manager_onboarding.html", agents=AgentDesk.AGENTS,
                           profiles=CLIENT_PROFILES, tiers=ClientConsole.TIERS,
                           account_fields=ClientConsole.ACCOUNT_FIELDS,
                           statuses=ClientConsole.STATUSES,
                           **_manager_context())


@app.route("/manager/settings")
@role_required("manager")
def manager_settings():
    return render_template("manager_settings.html", **_manager_context())


@app.route("/manager/route", methods=["POST"])
@role_required("manager")
def manager_route():
    """Assign a client book to a team lead, or an agent to a team lead."""
    payload = request.get_json(silent=True) or request.form
    lead = (payload.get("lead") or "").strip()
    if lead not in TEAM_LEADS:
        return jsonify({"ok": False, "error": f"Unknown team lead '{lead}'"}), 400

    client = (payload.get("client") or "").strip()
    agent = (payload.get("agent") or "").strip()

    if client:
        if client not in TeamConsole.BUCKET_LABELS:
            return jsonify({"ok": False, "error": f"Unknown client '{client}'"}), 404
        CLIENT_ROUTING[client] = lead
    elif agent:
        if agent not in AgentDesk.AGENTS:
            return jsonify({"ok": False, "error": f"Unknown agent '{agent}'"}), 404
        # An agent reports to exactly one lead, so take them off the others.
        for name in TEAM_LEADS:
            LEAD_AGENTS.setdefault(name, [])
            if agent in LEAD_AGENTS[name]:
                LEAD_AGENTS[name].remove(agent)
        LEAD_AGENTS[lead].append(agent)
        LEAD_AGENTS[lead].sort()
    else:
        return jsonify({"ok": False, "error": "Nothing to assign."}), 400

    _save_routing()
    return jsonify({"ok": True, "lead": lead, "client": client, "agent": agent,
                    "lead_agents": LEAD_AGENTS,
                    "client_routing": CLIENT_ROUTING})


# Separate from manager_route rather than another branch inside it: that route
# rejects a payload with no team lead as its first statement, so a tier change
# would be turned away before it was ever read.
@app.route("/manager/client/tier", methods=["POST"])
@role_required("manager")
def manager_client_tier():
    """Move a client book onto a different service tier."""
    payload = request.get_json(silent=True) or request.form
    client = (payload.get("client") or "").strip()
    tier = (payload.get("tier") or "").strip()

    if tier not in ClientConsole.TIERS:
        return jsonify({"ok": False, "error": f"Unknown tier '{tier}'"}), 400
    if client not in CLIENT_PROFILES:
        return jsonify({"ok": False, "error": f"Unknown client '{client}'"}), 404

    CLIENT_PROFILES[client]["tier"] = tier
    _save_client_profiles()
    return jsonify({"ok": True, "client": client, "tier": tier,
                    "label": ClientConsole.TIERS[tier]["label"]})


@app.route("/manager/client/account", methods=["POST"])
@role_required("manager")
def manager_client_account():
    """Update a client's merchant account record."""
    payload = request.get_json(silent=True) or request.form
    client = (payload.get("client") or "").strip()
    if client not in CLIENT_PROFILES:
        return jsonify({"ok": False, "error": f"Unknown client '{client}'"}), 404

    fields = payload.get("account")
    if not isinstance(fields, dict):
        fields = {k: payload.get(k) for k in ClientConsole.ACCOUNT_FIELDS
                  if payload.get(k) is not None}
    fields = {k: str(v).strip() for k, v in fields.items()
              if k in ClientConsole.ACCOUNT_FIELDS}
    if not fields:
        return jsonify({"ok": False, "error": "Nothing to update."}), 400

    # Unlike /merchant-config, a blank value clears the field. An account that
    # is no longer pending with anyone has to be able to say so.
    CLIENT_PROFILES[client]["account"].update(fields)
    _save_client_profiles()
    return jsonify({"ok": True, "client": client,
                    "account": CLIENT_PROFILES[client]["account"]})


@app.route("/ingest")
def ingest():
    # Quick mode only. The 1,000-orders + 12-chargebacks pipeline demo used to
    # live behind ?mode=full; it cleared CASES and replaced the working set with
    # 12 fabricated cases, which silently wiped whatever had been ingested.
    #
    # Built from the loaded book rather than IngestionDemo's ten hardcoded
    # cases: this is the page an upload is made from, so it has to be the page
    # that shows the upload landing. It used to report 10 no matter what.
    data = IngestConsole.compute(AIValidationEngine.get_pipeline_stats(CASES))
    data["orders_meta"] = None
    return render_template("ingest.html", d=data, mode="quick", merchant=MERCHANT_CONFIG)


def _first(row, *names, default=""):
    """First non-empty value among `names`.

    The dispute sheets have shipped under two spellings: an early export
    truncated its headers (`ReasonCategor`, `ChargebackAm`, `ChannelName`),
    the current one spells them out. Trying each name in turn lets one
    normalizer read both instead of silently returning blanks for whichever
    file it wasn't written against.
    """
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return default


def _last_four(masked):
    """Trailing digits of a masked PAN like '413278XXXXXX5506'."""
    digits = [ch for ch in (masked or "") if ch.isdigit()]
    return "".join(digits[-4:]) if len(digits) >= 4 else ""


def _normalize_chargeback_row(row):
    """Auto-detect CSV format and normalize to standard fields."""
    headers = set(row.keys())

    # Format B: DisputeId / ReasonCode / CardType / ProcessorName columns
    if "DisputeId" in headers or "ChannelName" in headers or "DisputeStage" in headers:
        card_type = _first(row, "CardScheme", "CardType", default="Visa")
        reason_code = _first(row, "ReasonCode")
        category = _first(row, "ReasonCategory", "ReasonCategor")
        stage = _first(row, "DisputeStage", default="CB")
        win_rate = _safe_float(_first(row, "WinRate", default="0"))

        # Map category to scenario
        cat_map = {
            "fraud": "Fraud - Card Not Present (CNP)",
            "authorization": "Fraud - No Authorization",
            "merchandise": "Merchandise - Item Not Received",
            "processing": "Processing - Incorrect Amount",
            "others": "Other Dispute",
        }
        scenario = cat_map.get(category.lower(), category.title() if category else "Unknown")

        # Stage label
        stage_map = {"CB": "Chargeback", "PRE_ARB": "Pre-Arbitration", "RFI": "Request for Information"}
        stage_label = stage_map.get(stage, stage)

        due_date = _first(row, "DueDate", "RepresentmentDeadline")

        return {
            "dispute_ref": _first(row, "DisputeId"),
            "reason_code": reason_code,
            "reason_description": _first(row, "ReasonMsg"),
            "card_scheme": card_type.title() if card_type.isupper() else card_type,
            "card_last_four": _last_four(_first(row, "CardNumberMasked")),
            "disputed_amount": _safe_float(_first(row, "ChargebackAmount", "ChargebackAm",
                                                  "ChargebackTxnAmount", default="0")),
            "txn_original_amount": _safe_float(_first(row, "ChargebackTxnAmount", default="0")),
            "transaction_date": _first(row, "TransactionTime", "TransactionTim"),
            "dispute_date": _first(row, "DisputeTime"),
            "due_date": due_date.split(" ")[0] if due_date else "",
            "arn": _first(row, "ARN"),
            "processor": _first(row, "ProcessorName", "ChannelName", default="Unknown"),
            # The current sheet carries a real order reference; the older one only
            # had the opaque TransactionChannel token.
            "order_id": _first(row, "OrderId", "TransactionChannel", "TransactionCha"),
            "payment_ref": _first(row, "PspReferenceId", "MerchantUserId"),
            "scenario": scenario,
            "category": category,
            "stage": stage_label,
            "currency": _first(row, "ChargebackTxnCurrency", "ChargebackTxn", default="USD"),
            "txn_amount": _safe_float(_first(row, "ChargebackTxnAmount", default="0")),
            "refund_type": _first(row, "RefundPayType"),
            "win_rate": win_rate,
            "win_probability": max(1, int(win_rate * 10000)) if win_rate > 0 else 50,
            "status": _first(row, "DisputeStatus", default="NEED_RESPONSE"),
            # Sheets without these columns fall back to an undecided case.
            "case_outcome": _first(row, "CaseOutcome", default="Decision Pending"),
            "outcome_date": _first(row, "OutcomeDate"),
            # Everything else the sheet carries. Kept whole rather than flattened
            # into ~40 more keys: the evidence documents read straight from it.
            "source": dict(row),
        }

    # Format A: dispute_ref / reason_code / card_scheme columns (original)
    return {
        "dispute_ref": row.get("dispute_ref", ""),
        "reason_code": row.get("reason_code", "13.1"),
        "reason_description": row.get("reason_description", ""),
        "card_scheme": row.get("card_scheme", "Visa"),
        "card_last_four": row.get("card_last_four", ""),
        "disputed_amount": _safe_float(row.get("disputed_amount", 0)),
        "transaction_date": row.get("transaction_date", ""),
        "dispute_date": row.get("dispute_date", ""),
        "due_date": "",
        "arn": row.get("arn", ""),
        "processor": row.get("processor", "Unknown"),
        "order_id": row.get("order_id", ""),
        "payment_ref": row.get("payment_ref", ""),
        "scenario": "",
        "category": "",
        "stage": "Chargeback",
        "currency": "USD",
        "txn_amount": _safe_float(row.get("disputed_amount", 0)),
        "txn_original_amount": _safe_float(row.get("disputed_amount", 0)),
        "refund_type": "",
        "win_rate": 0,
        "win_probability": 50,
        "status": "NEED_RESPONSE",
        "case_outcome": "Decision Pending",
        "outcome_date": "",
        "source": dict(row),
    }


def _build_cases_from_rows(chargebacks):
    """Turn raw chargeback CSV rows into case dicts.

    Shared by the startup loader and the /ingest/upload route so the two
    ingestion paths cannot drift apart.
    """
    # Use existing orders data for enrichment (if order_id matches)
    all_orders = ChargebackCaseLoader.load_orders()
    orders_by_id = {o["order_id"]: o for o in all_orders}

    new_cases = []
    for idx, raw_row in enumerate(chargebacks):
        cb = _normalize_chargeback_row(raw_row)

        order_id = cb["order_id"]
        raw = orders_by_id.get(order_id, {})
        reason_code = cb["reason_code"]
        reason_info = REASON_CODES.get(reason_code, {})
        disputed_amount = cb["disputed_amount"]
        desc = cb["reason_description"]
        scenario = desc or cb["scenario"] or (reason_info.get("scenarios", ["Unknown"])[0] if reason_info.get("scenarios") else "Unknown")

        case_id = cb["dispute_ref"] or f"CSV-{idx + 1:04d}"
        card_last4 = cb["card_last_four"] or raw.get("card_last_four", "0000")

        src = cb.get("source") or {}

        # Determine authentication signals. Preference order: the dispute sheet
        # itself (the current export carries AVS/CVV/3DS and delivery columns),
        # then the orders file, then inference from the reason category for the
        # older sheets that carry neither.
        has_order = bool(raw)
        if src.get("AvsResult") or src.get("ThreeDSStatus"):
            avs_pass = (src.get("AvsResult", "").startswith("Y")
                        and src.get("CvvResult", "").startswith("M"))
            delivered = src.get("DeliveryStatus", "").lower().startswith(
                ("delivered", "digital delivery"))
        elif has_order:
            avs_pass = raw.get("avs_cvv_match", "") == "Pass"
            delivered = raw.get("fulfillment_status", "") == "Delivered"
        else:
            # Infer from category and reason description for uploaded cases
            cat = (cb.get("category", "") or "").lower()
            desc_lower = desc.lower()

            if cat == "fraud" or any(k in desc_lower for k in ["fraud", "unauthorized", "ato", "not recognized"]):
                # Fraud: only winnable if 3DS was used (liability shift to issuer)
                avs_pass = False
                delivered = False
            elif cat == "authorization" or any(k in desc_lower for k in ["no authorization", "purchase_unauthorized"]):
                avs_pass = False
                delivered = False
            elif cat == "others" or any(k in desc_lower for k in ["other", "unknown", "inquiry", "noncompliant", "unrecognizable"]):
                avs_pass = False
                delivered = False
            elif cat == "merchandise" or any(k in desc_lower for k in [
                "not received", "not as described", "defective", "unsatisfactory",
                "damaged", "cancelled", "return", "faulty"
            ]):
                avs_pass = True
                delivered = "not received" not in desc_lower and "cancelled" not in desc_lower
            elif cat == "processing" or any(k in desc_lower for k in [
                "incorrect amount", "duplicate", "credit not processed", "processing error"
            ]):
                # Processing: AVS may pass but needs human verification (HITL)
                avs_pass = True
                delivered = False  # No auto-delivery, forces HITL not Auto
            else:
                avs_pass = False
                delivered = False

        # Two vocabularies for the same fact. `case_status` is the reporting
        # deck's wording, which keeps "Not Fought" distinct; `outcome` is what
        # the rest of the app already speaks, where a conceded case is a refund.
        case_status = cb["case_outcome"] or "Decision Pending"
        outcome = {"Won": "Win", "Lost": "Lost",
                   "Not Fought": "Refunded"}.get(case_status, "Pending")

        new_case = {
            "case_id": case_id,
            "scenario": scenario,
            "chargeback_category": desc or reason_info.get("title", cb["category"] or "Unknown"),
            # Raw ReasonCategor from the sheet (fraud / merchandise / processing /
            # authorization / others) — drives the Agent Desk category filter.
            "reason_category": cb["category"] or "Unknown",
            "reason_code": reason_code,
            "processor": cb["processor"],
            "amount": disputed_amount,
            "win_probability": cb["win_probability"],
            "submission_date": cb["dispute_date"],
            "submission_status": "Submitted" if case_status != "Decision Pending" else "Pending",
            "outcome": outcome,
            "case_status": case_status,
            "outcome_date": cb["outcome_date"],
            "merchant": MERCHANT_CONFIG["company_name"],
            "merchant_account": MERCHANT_CONFIG["merchant_account_number"],
            "descriptor_name": MERCHANT_CONFIG["dba_name"],
            "descriptor_url": MERCHANT_CONFIG["descriptor_url"],
            "payment_method": cb["card_scheme"],
            "card_last_four": card_last4,
            "card_expiry": "",
            "cardholder": src.get("UserFullName") or "***REDACTED***",
            "issuer_country": src.get("IssuerCountry") or "United States",
            "issuer_name": src.get("IssuerName", ""),
            "avs_response": src.get("AvsResult") or (
                "Both postal code and address match (Y)" if avs_pass else "No match"),
            "cvv_response": src.get("CvvResult") or (
                "Supplied, Matches (M)" if avs_pass else "Not provided"),
            "threed_secure": src.get("ThreeDSStatus") or (
                "Authenticated" if (avs_pass and delivered) else "Not Offered"),
            "transaction_date": cb["transaction_date"],
            "amount_authorized": cb.get("txn_original_amount") or cb["txn_amount"] or disputed_amount,
            "amount_settled": round(disputed_amount * 0.85, 2) if disputed_amount > 50 else disputed_amount,
            "dispute_psp_ref": cb["dispute_ref"] or case_id,
            "payment_psp_ref": cb["payment_ref"],
            "dispute_creation_date": cb["dispute_date"],
            "order_id": order_id,
            "acquirer_ref": cb["arn"] or "N/A",
            "acquirer_code": src.get("AcquirerMID", ""),
            "acquirer_name": src.get("AcquirerName", ""),
            "auth_code": src.get("AuthorizationCode", ""),
            "cardholder_email": src.get("UserEmail", ""),
            "cardholder_phone": src.get("UserPhone", ""),
            "auto_defended": delivered and avs_pass,
            "liability_shift": (src["ThreeDSLiabilityShift"] == "Yes"
                                if src.get("ThreeDSLiabilityShift") else avs_pass),
            "issuer_comments": "",
            "dispute_stage": cb["stage"],
            "dispute_status": cb["status"],
            "due_date": cb["due_date"],
            "currency": cb["currency"],
            "refund_type": cb["refund_type"],
            "win_rate": cb["win_rate"],
            "reason_description": cb["reason_description"],
            # Full sheet row, kept intact so the evidence documents can read the
            # order, delivery, refund and account columns without the case dict
            # having to mirror all 89 of them.
            "source": src,
            "dispute_history": [
                {"event": "CaseCreated", "date": cb["dispute_date"]},
                {"event": "CSVUpload", "date": datetime.now().strftime("%b %d, %Y, %H:%M:%S")},
            ],
        }
        new_cases.append(new_case)

    return new_cases


def _apply_cases(new_cases, source="automated", merge=False):
    """Swap in — or merge in — a case set and recompute cached evidence.

    `source` tags every case as system-automated or manually ingested, which is
    what lets the management hub report a holistic total across both.

    `merge=True` keeps the cases already loaded: a case id that is already known
    is updated in place and keeps its original source, anything new is appended.
    Without it an upload would silently discard the existing book.

    Agent decisions are re-applied before classification so the restored status
    feeds evidence and routing. They are keyed by case id, so both a merge and a
    replace keep the work an agent has already done.
    """
    global EVIDENCE_RESULTS, EVIDENCE_STATS

    if merge:
        existing = {c["case_id"]: c for c in CASES}
        for case in new_cases:
            known = existing.get(case["case_id"])
            if known is None:
                case["ingest_source"] = source
                CASES.append(case)
            else:
                # Keep how this case originally arrived, and keep the fields a
                # lead or agent has since set on it.
                keep = {k: known[k] for k in
                        ("ingest_source", "assigned_agent", "rework_released",
                         "agent_action", "agent_action_at") if k in known}
                known.update(case)
                known.update(keep)
    else:
        for case in new_cases:
            case["ingest_source"] = source
        CASES.clear()
        CASES.extend(new_cases)

    _restore_routing()
    # Second, beside routing: both are client-keyed manager-owned state, and
    # neither depends on the case list. Everything below this line does.
    _restore_client_profiles()
    _restore_allocations()
    _restore_rework_releases()
    _restore_agent_actions()
    _classified = AIValidationEngine.classify_all(CASES)
    EVIDENCE_RESULTS = EvidenceCollectionEngine.collect_all(_classified)
    EVIDENCE_STATS = EvidenceCollectionEngine.get_aggregate_stats(EVIDENCE_RESULTS)
    # An upload changes the book under everyone, so it counts as a change even
    # though nothing here went through one of the _save_* paths.
    _bump_state()


def _load_startup_cases():
    """Load the working data set at boot so every page has the full queue
    without needing a manual upload. Falls back to the seeded cases if the
    file is missing or unreadable."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", STARTUP_DATASET)
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
    except OSError:
        return 0
    if not rows:
        return 0
    _apply_cases(_build_cases_from_rows(rows))
    return len(CASES)


@app.route("/ingest/upload", methods=["POST"])
def ingest_upload():
    chargebacks_file = request.files.get("chargebacks_csv")

    if not chargebacks_file:
        flash("Please upload a Chargebacks CSV file.", "error")
        return redirect(url_for("ingest"))

    try:
        chargebacks_text = io.StringIO(chargebacks_file.read().decode("utf-8"))
        chargebacks = list(_csv.DictReader(chargebacks_text))
    except Exception as e:
        flash(f"Error reading CSV file: {e}", "error")
        return redirect(url_for("ingest"))

    if not chargebacks:
        flash("CSV file appears to be empty.", "error")
        return redirect(url_for("ingest"))

    # Merge rather than replace: an upload tops up the book instead of wiping
    # whatever is already loaded, and its rows are tagged as manually ingested.
    before = len(CASES)
    _apply_cases(_build_cases_from_rows(chargebacks), source="manual", merge=True)
    added = len(CASES) - before
    flash(f"Ingested {len(chargebacks)} rows — {added} new case(s), "
          f"{len(chargebacks) - added} updated.", "success")
    return redirect(url_for("dashboard"))



@app.route("/executive")
def executive():
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    ex = ExecutiveAnalytics.compute(CASES, ml_stats, EVIDENCE_STATS)
    return render_template("executive.html", ex=ex, ml=ml_stats)


@app.route("/agent-desk")
@role_required("admin", "manager")
def agent_desk():
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    desk = AgentDesk.build_queue(CASES, ml_stats, EVIDENCE_RESULTS)
    active = request.args.get("agent") or (desk["agents"][0] if desk["agents"] else "")
    return render_template("agent_desk.html", d=desk, active_agent=active,
                           actions=AGENT_ACTIONS)


def _case_owner(case):
    """Which agent owns this case — a lead's allocation, else the hash."""
    return (case.get("assigned_agent")
            or AgentDesk.AGENTS[deterministic_seed(case["case_id"]) % len(AgentDesk.AGENTS)])


def _current_agent():
    """Which queue the signed-in user owns.

    A manager opening the agent pages is inspecting someone's workspace, so
    they get the default queue rather than nothing at all.
    """
    return AGENT_LOGIN_MAP.get(session.get("user", ""), DEFAULT_AGENT)


def _current_client():
    """Which client book the signed-in user owns, or None.

    No default, unlike _current_agent: falling back to a brand would show one
    merchant another's disputes. The label is checked against the live roster
    rather than trusted, so a stale mapping resolves to nothing rather than to
    a book that no longer exists.
    """
    label = CLIENT_LOGIN_MAP.get(session.get("user", ""))
    return label if label in TeamConsole.BUCKET_LABELS else None


def _client_console():
    """Everything the signed-in client may see, or None if they own no book."""
    client = _current_client()
    if not client:
        return None
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    return ClientConsole.for_client(CASES, ml_stats, EVIDENCE_RESULTS, client,
                                    CLIENT_PROFILES.get(client, {}))


def _agent_console(year=None, month=None):
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    return AgentConsole.for_agent(CASES, ml_stats, EVIDENCE_RESULTS,
                                  _current_agent(), year=year, month=month)


@app.route("/agent/dashboard")
@role_required("agent", "manager")
def agent_dashboard():
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)
    a = _agent_console(year=year, month=month)
    return render_template("agent_dashboard.html", a=a,
                           period=request.args.get("period", "daily"))


@app.route("/agent/chargebacks")
@role_required("agent", "manager")
def agent_chargebacks():
    return render_template("agent_chargebacks.html", a=_agent_console(),
                           agent_actions=AGENT_ACTIONS)


def _repository_library():
    """The template content the repository page lists, from its real sources.

    Both tiles the page keeps are backed by content that already ships: the
    reason-code cover letters and the generated evidence documents. Reading
    them here rather than hardcoding a tile means the page cannot drift from
    what a case would actually pull.
    """
    letters = []
    for key, body in COVER_LETTER_BODIES.items():
        letters.append({
            "key": key,
            "label": key.replace("_", " ").title(),
            "subheading": body.get("subheading", ""),
            "intro": body.get("intro", ""),
            "primary_label": body.get("primary_defense_label", ""),
            "primary_text": body.get("primary_defense_text", ""),
            "secondary_label": body.get("secondary_defense_label", ""),
            "secondary_text": body.get("secondary_defense_text", ""),
            "points": body.get("defense_points", []),
            "conclusion": body.get("conclusion", ""),
        })

    documents = [{
        "key": key,
        "title": doc.get("title", ""),
        "icon": doc.get("icon", ""),
        "blurb": doc.get("blurb", ""),
        # Shown as a column list so a reader can see what the generated
        # document actually contains before pulling it into a packet.
        "columns": [c.strip() for c in (doc.get("columns") or "").split(",")
                    if c.strip()],
        "policy": bool(doc.get("policy")),
    } for key, doc in DOCUMENTS.items()]

    return {"cover_letters": letters, "documents": documents}


@app.route("/client/dashboard")
@role_required("client")
def client_dashboard():
    c = _client_console()
    if c is None:
        # A client login pointing at no book is a misconfiguration, not a
        # permission question. Drop the session rather than render an empty
        # portal that looks like the merchant has no disputes.
        session.clear()
        return redirect(url_for("portal"))
    return render_template("client_dashboard.html", c=c)


@app.route("/client/chargebacks")
@role_required("client")
def client_chargebacks():
    c = _client_console()
    if c is None:
        session.clear()
        return redirect(url_for("portal"))
    return render_template("client_chargebacks.html", c=c)


@app.route("/client/case/<case_id>")
@role_required("client")
def client_case(case_id):
    client = _current_client()
    case = _find_case(case_id)
    # 404 rather than 403: a client must not be able to learn which case ids
    # exist in another brand's book by watching the status change.
    if (not client or not case
            or ClientConsole.client_of(case, TeamConsole._bucket_map(CASES)) != client):
        abort(404)
    return render_template("client_case.html", c=_client_console(), case=case)


@app.route("/agent/repository")
@role_required("agent", "manager")
def agent_repository():
    return render_template("agent_repository.html", a=_agent_console(),
                           lib=_repository_library())


@app.route("/agent/settings")
@role_required("agent", "manager")
def agent_settings():
    return render_template("agent_settings.html", a=_agent_console())


def _team_console():
    """Scoped to the signed-in lead's own agents and client books.

    A manager opening these pages is inspecting the whole operation, so they
    are not narrowed to one lead's roster.
    """
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    user = session.get("user", "")
    if session.get("role") == "manager":
        my_agents, my_clients = None, list(CLIENT_ROUTING)
    else:
        my_agents = LEAD_AGENTS.get(user, [])
        my_clients = [b for b, owner in CLIENT_ROUTING.items() if owner == user]
    return TeamConsole.for_lead(CASES, ml_stats, EVIDENCE_RESULTS,
                                my_agents=my_agents, my_clients=my_clients)


@app.route("/admin/dashboard")
@role_required("admin", "manager")
def admin_dashboard():
    return render_template("admin_dashboard.html", t=_team_console())


@app.route("/admin/allocation")
@role_required("admin", "manager")
def admin_allocation():
    return render_template("admin_allocation.html", t=_team_console(),
                           agents=AgentDesk.AGENTS)


@app.route("/admin/approvals")
@role_required("admin", "manager")
def admin_approvals():
    return render_template("admin_approvals.html", t=_team_console())


@app.route("/admin/repository")
@role_required("admin", "manager")
def admin_repository():
    return render_template("admin_repository.html", t=_team_console(),
                           lib=_repository_library())


@app.route("/admin/allocate", methods=["POST"])
@role_required("admin", "manager")
def admin_allocate():
    """Re-assign one or more cases to an agent."""
    payload = request.get_json(silent=True) or request.form
    agent = (payload.get("agent") or "").strip()
    ids = payload.get("case_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if agent not in AgentDesk.AGENTS:
        return jsonify({"ok": False, "error": f"Unknown agent '{agent}'"}), 400

    moved = []
    for case_id in ids:
        case = _find_case((case_id or "").strip())
        if case is None:
            continue
        case["assigned_agent"] = agent
        moved.append(case["case_id"])
    if not moved:
        return jsonify({"ok": False, "error": "No matching cases."}), 404

    _save_allocations()
    return jsonify({"ok": True, "agent": agent, "moved": moved,
                    "count": len(moved)})


@app.route("/admin/rework", methods=["POST"])
@role_required("admin", "manager")
def admin_rework():
    """Release a submitted case for rework, or take the release back."""
    payload = request.get_json(silent=True) or request.form
    case_id = (payload.get("case_id") or "").strip()
    grant = str(payload.get("grant", "true")).lower() not in ("false", "0", "no")

    case = _find_case(case_id)
    if case is None:
        return jsonify({"ok": False, "error": f"Unknown case '{case_id}'"}), 404

    if grant:
        at = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
        case["rework_released"] = {"released_by": session.get("user", ""), "at": at}
        case.setdefault("dispute_history", []).append(
            {"event": f"ReworkReleased by {session.get('user', '')}", "date": at})
    else:
        case.pop("rework_released", None)

    _save_rework_releases()
    return jsonify({"ok": True, "case_id": case_id, "released": grant,
                    "released_by": (case.get("rework_released") or {}).get("released_by", ""),
                    "at": (case.get("rework_released") or {}).get("at", "")})


def _find_case(case_id):
    return next((c for c in CASES if c["case_id"] == case_id), None)


@app.route("/agent-desk/action", methods=["POST"])
def agent_desk_action():
    """Record an agent's decision on a case.

    The decision rewrites the case's reporting status, so Manager Hub and the
    dashboard reflect it immediately, and is written to disk so it survives a
    restart.
    """
    payload = request.get_json(silent=True) or request.form
    case_id = (payload.get("case_id") or "").strip()
    action = (payload.get("action") or "").strip()

    case = _find_case(case_id)
    if case is None:
        return jsonify({"ok": False, "error": f"Unknown case '{case_id}'"}), 404
    if action not in AGENT_ACTION_EFFECTS:
        return jsonify({"ok": False, "error": f"Unknown action '{action}'"}), 400

    # The two agent-level restrictions from the spec, enforced here rather than
    # only in the template — the UI hides these controls, but hiding a control
    # is not a rule. A team lead or manager is exempt: releasing a submitted
    # case for rework is precisely their job.
    if session.get("role") == "agent":
        if _case_owner(case) != _current_agent():
            return jsonify({"ok": False,
                            "error": "That case belongs to another agent."}), 403
        if ((case.get("submission_status") or "") == "Submitted"
                and not case.get("rework_released")):
            return jsonify({"ok": False,
                            "error": "Already submitted — rework needs "
                                     "team-lead approval."}), 403

    at = _apply_agent_action(case, action)
    # A release is a one-shot: the agent fixes the case and resubmits, and the
    # file locks again. Re-opening it needs another approval, as the spec says.
    if session.get("role") == "agent" and case.pop("rework_released", None):
        _save_rework_releases()
    case.setdefault("dispute_history", []).append(
        {"event": f"AgentAction: {action}", "date": at})
    _save_agent_actions()

    return jsonify({"ok": True, "case_id": case_id, "action": action, "at": at,
                    "case_status": case["case_status"],
                    "outcome": case["outcome"],
                    "submission_status": case["submission_status"]})


@app.route("/agent-desk/evidence", methods=["POST"])
def agent_desk_evidence():
    """Attach a manually uploaded evidence file to a case.

    Records the file's name, size and timestamp only — the bytes are not
    written to disk. This is a demo; persisting arbitrary uploads into the
    project folder would add risk without adding capability.
    """
    case_id = (request.form.get("case_id") or "").strip()
    upload = request.files.get("evidence_file")

    case = _find_case(case_id)
    if case is None:
        return jsonify({"ok": False, "error": f"Unknown case '{case_id}'"}), 404
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "No file supplied"}), 400

    size = len(upload.read())
    attachment = {
        "filename": upload.filename,
        "size_kb": round(size / 1024, 1),
        "uploaded_by": request.form.get("agent", "Agent"),
        "uploaded_at": datetime.now().strftime("%b %d, %Y, %H:%M:%S"),
    }
    case.setdefault("manual_evidence", []).append(attachment)
    case.setdefault("dispute_history", []).append(
        {"event": f"EvidenceUploaded: {upload.filename}", "date": attachment["uploaded_at"]})

    return jsonify({"ok": True, "attachment": attachment,
                    "total": len(case["manual_evidence"])})


@app.route("/qa-review")
@role_required("manager")
def qa_review():
    ml_stats = AIValidationEngine.get_pipeline_stats(CASES)
    qa = QAReviewEngine.compute(CASES, ml_stats, EVIDENCE_RESULTS, REASON_CODES)
    return render_template("qa_review.html", qa=qa)


@app.route("/case/<case_id>")
def case_detail(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    reason = _get_reason(case)
    ml = AIValidationEngine.classify(case)

    orders = ChargebackCaseLoader.load_orders()
    orders_by_id = {o["order_id"]: o for o in orders}
    raw = orders_by_id.get(case.get("order_id", ""), {})
    order = {
        "product_name": raw.get("product_name", "N/A"),
        "product_id": raw.get("product_id", "N/A"),
        "product_category": raw.get("product_category", "N/A"),
        "quantity": raw.get("quantity", "1"),
        "unit_price": raw.get("unit_price", ""),
        "return_policy_days": raw.get("return_policy_days", "30"),
        "customer_id": raw.get("customer_id", "N/A"),
        "customer_email": raw.get("customer_email", "N/A"),
        "customer_city": raw.get("customer_city", "N/A"),
        "customer_state": raw.get("customer_state", "N/A"),
        "customer_ip": raw.get("customer_ip", "N/A"),
        "fulfillment_status": raw.get("fulfillment_status", "N/A"),
        "shipping_carrier": raw.get("shipping_carrier", "N/A"),
        "tracking_number": raw.get("tracking_number", "N/A"),
        "delivery_date": raw.get("delivery_date", "N/A"),
        "delivery_signed": raw.get("delivery_signed", "N/A"),
    }
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    return render_template("case_detail.html", case=case, reason=reason, ml=ml, order=order, fetched_at=fetched_at)


@app.route("/chargeback/<case_id>")
def chargeback_detail(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    ml = AIValidationEngine.classify(case)
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template("chargeback_detail.html", case=case, ml=ml, fetched_at=fetched_at)


# ─── Counter evidence ──────────────────────────────────────────────────────────
# Manual evidence is written to disk so it can be downloaded back. Everything
# below assumes the filename and case id are hostile input.
UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "static", "uploads")
ALLOWED_UPLOAD_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".eml", ".msg",
                      ".csv", ".txt", ".docx", ".xlsx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _slug(text, fallback="general"):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or fallback


# The letter's fixed sections name their own evidence requirement, so the
# template needs the same slug the upload route stores files under.
app.jinja_env.filters["slug"] = _slug


def _case_upload_dir(case_id, create=False):
    """Folder holding one case's manual uploads.

    The case id comes from the sheet and is used as a directory name, so it is
    reduced to word characters first — a crafted id must not be able to walk out
    of static/uploads.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", case_id or "")[:64]
    if not safe:
        return None
    path = os.path.join(UPLOAD_ROOT, safe)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _list_uploads(case_id):
    """Manual uploads on disk for a case, newest last.

    The requirement each file answers is stored as a filename prefix
    ('<slug>__<name>') — this app has no database, and the alternative is
    losing the association on restart.
    """
    folder = _case_upload_dir(case_id)
    if not folder or not os.path.isdir(folder):
        return []

    uploads = []
    for stored in sorted(os.listdir(folder)):
        full = os.path.join(folder, stored)
        if not os.path.isfile(full):
            continue
        slug, _, original = stored.partition("__")
        info = os.stat(full)
        uploads.append({
            "stored": stored,
            "slug": slug,
            "filename": original or stored,
            "size_kb": round(info.st_size / 1024, 1),
            "uploaded_at": datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y, %H:%M"),
            "url": url_for("counter_upload_download", case_id=case_id, filename=stored),
            "delete_url": url_for("counter_upload_delete", case_id=case_id, filename=stored),
        })
    return uploads


@app.route("/counter/<case_id>")
def counter_evidence(case_id):
    case = _find_case(case_id)
    if not case:
        return "Case not found", 404
    from chargeback.engines.evidence_rules import (get_evidence_for_case, DATABASES,
                                                   calculate_winning_ratio)
    from chargeback.engines.cover_letter import build_evidence_list
    from chargeback.engines.evidence_documents import build_documents

    ev_info = get_evidence_for_case(case)
    dynamic_evidence = build_evidence_list(case)
    documents = build_documents(case, MERCHANT_CONFIG)

    uploads = _list_uploads(case_id)
    by_slug = defaultdict(list)
    for up in uploads:
        by_slug[up["slug"]].append(up)

    # The client's service tier decides how this packet is built, so it has to
    # be resolved before anything is called available.
    mapping = TeamConsole._bucket_map(CASES)
    client = ClientConsole.client_of(case, mapping)
    tier = ClientConsole.tier_of(case, CLIENT_PROFILES, mapping)
    section_modes, section_labels = ClientConsole.section_plan(tier, documents, case)

    # Split the reason code's rule set: items a generated document satisfies are
    # already in hand, the rest need an agent to upload something. An item counts
    # as available only when the document actually built or a file actually
    # landed — never just because the rule names it.
    #
    # The tier overrides the engine's own system/manual call. On a manual
    # account there are no API keys behind these documents, so an item a
    # document could satisfy still has to be uploaded like any other — claiming
    # it was fetched would be a lie told to the person assembling the packet.
    scored, claimed_slugs = [], set()
    for item in ev_info["evidence"]:
        doc_key = item.get("doc_key")
        if doc_key and section_modes.get(doc_key) == "system":
            source = "system"
            available = documents[doc_key]["available"]
        else:
            source = "manual"
            slug = _slug(item["name"])
            claimed_slugs.add(slug)
            available = bool(by_slug.get(slug))
        scored.append({**item, "source": source, "available": available})

    # The letter's nine sections own their slugs in every mode, not just the
    # modes that render an upload box. A file uploaded before the client moved
    # to a hands-off tier stays attached to its section instead of being
    # orphaned into "other attachments".
    claimed_slugs.update(_slug(label) for label in section_labels.values())
    other_uploads = [u for u in uploads if u["slug"] not in claimed_slugs]

    # The letter addresses documents one at a time, so hand the template the
    # slug -> uploads map as well; each manual section pulls just its own files.
    return render_template("counter_evidence.html", case=case, ev=ev_info,
                           dynamic_evidence=dynamic_evidence, databases=DATABASES,
                           documents=documents, uploads_by_slug=by_slug,
                           other_uploads=other_uploads, evidence_items=scored,
                           ratio=calculate_winning_ratio(scored),
                           tier=tier, client=client,
                           tier_label=ClientConsole.TIERS[tier]["label"],
                           tier_blurb=ClientConsole.TIERS[tier]["blurb"],
                           section_modes=section_modes, section_labels=section_labels,
                           upload_exts=sorted(ALLOWED_UPLOAD_EXT),
                           max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024))


@app.route("/counter/<case_id>/upload", methods=["POST"])
def counter_upload(case_id):
    """Store a manually supplied evidence file for a case."""
    case = _find_case(case_id)
    if not case:
        abort(404)

    upload = request.files.get("evidence_file")
    if not upload or not upload.filename:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("counter_evidence", case_id=case_id))

    name = secure_filename(upload.filename)
    if not name:
        flash("That filename is not usable.", "error")
        return redirect(url_for("counter_evidence", case_id=case_id))

    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        flash(f"{ext or 'That file type'} is not accepted. Allowed: "
              f"{', '.join(sorted(ALLOWED_UPLOAD_EXT))}.", "error")
        return redirect(url_for("counter_evidence", case_id=case_id))

    # Size from the stream rather than reading it all in first.
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        flash(f"File is too large ({round(size / 1024 / 1024, 1)} MB). "
              f"Limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", "error")
        return redirect(url_for("counter_evidence", case_id=case_id))

    folder = _case_upload_dir(case_id, create=True)
    if not folder:
        abort(404)

    stored = f"{_slug(request.form.get('requirement'))}__{name}"
    upload.save(os.path.join(folder, stored))

    case.setdefault("manual_evidence", []).append({
        "filename": name,
        "size_kb": round(size / 1024, 1),
        "uploaded_by": session.get("user", "Agent"),
        "uploaded_at": datetime.now().strftime("%b %d, %Y, %H:%M:%S"),
    })
    case.setdefault("dispute_history", []).append(
        {"event": f"EvidenceUploaded: {name}",
         "date": datetime.now().strftime("%b %d, %Y, %H:%M:%S")})

    flash(f"Uploaded {name}.", "success")
    return redirect(url_for("counter_evidence", case_id=case_id))


INLINE_UPLOAD_EXT = {".png", ".jpg", ".jpeg"}


@app.route("/counter/<case_id>/upload/<filename>")
def counter_upload_download(case_id, filename):
    """Serve a manual upload back. send_from_directory rejects traversal.

    Images are served inline so the letter can show them as a thumbnail the way
    the page always has; every other type stays an attachment. Serving inline is
    only safe because the allowlist has no .html or .svg — those would run
    script on this origin.
    """
    folder = _case_upload_dir(case_id)
    if not folder or not os.path.isdir(folder):
        abort(404)
    inline = os.path.splitext(filename)[1].lower() in INLINE_UPLOAD_EXT
    return send_from_directory(folder, filename, as_attachment=not inline)


@app.route("/counter/<case_id>/upload/<filename>/delete", methods=["POST"])
def counter_upload_delete(case_id, filename):
    folder = _case_upload_dir(case_id)
    if not folder:
        abort(404)

    # Resolve and confirm the target really sits inside this case's folder.
    target = os.path.realpath(os.path.join(folder, filename))
    if os.path.commonpath([target, os.path.realpath(folder)]) != os.path.realpath(folder):
        abort(404)
    if not os.path.isfile(target):
        abort(404)

    name = filename.partition("__")[2] or filename
    try:
        os.remove(target)
    except OSError as exc:
        # Windows refuses to unlink a file another process still holds open.
        # Report it rather than handing the agent a 500 page.
        flash(f"Could not remove {name}: {exc.strerror or exc}.", "error")
    else:
        flash(f"Removed {name}.", "success")
    return redirect(url_for("counter_evidence", case_id=case_id))


@app.route("/review/<case_id>")
def review_packet(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    reason = _get_reason(case)
    return render_template("review_packet.html", case=case, reason=reason)


@app.route("/reason-codes")
def reason_codes():
    return render_template("reason_codes.html", codes=REASON_CODES)


@app.route("/reason-code/<code_id>")
def reason_code_detail(code_id):
    code = REASON_CODES.get(code_id)
    if not code:
        return "Reason code not found", 404
    return render_template("reason_code_detail.html", code_id=code_id, code=code)


@app.route("/processor/<case_id>")
def processor_view(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    return render_template("processor_view.html", case=case)


@app.route("/defend/<case_id>")
def defend_case(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    reason = _get_reason(case)
    return render_template("defend.html", case=case, reason=reason)


@app.route("/rebuttal/<case_id>")
def rebuttal(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    reason = _get_reason(case)
    auto_print = request.args.get("print", "") == "1"

    orders = ChargebackCaseLoader.load_orders()
    orders_by_id = {o["order_id"]: o for o in orders}

    def parse_date_only(value):
        dt = _parse_any_datetime(value)
        return dt.date() if dt else None

    def fmt_human_date(value, fallback=""):
        dt = _parse_any_datetime(value)
        if not dt:
            return fallback
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}"

    def fmt_human_datetime(value, fallback=""):
        dt = _parse_any_datetime(value)
        if not dt:
            return fallback
        ampm = dt.strftime("%p")
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}, {dt.strftime('%I:%M')} {ampm}"

    def safe_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def resolve_order_row():
        order_id = case.get("order_id", "")
        direct = orders_by_id.get(order_id)
        if direct:
            return direct, "order_id_exact", 100

        target_last4 = str(case.get("card_last_four", "")).strip()
        target_method = str(case.get("payment_method", "")).strip().lower()
        target_amount = _safe_float(case.get("amount_authorized", case.get("amount", 0)))
        target_date = parse_date_only(case.get("transaction_date", ""))

        best_row = None
        best_score = -1
        for row in orders:
            score = 0
            if target_last4 and row.get("card_last_four") == target_last4:
                score += 35
            if target_method and row.get("payment_method", "").strip().lower() == target_method:
                score += 20
            row_amount = _safe_float(row.get("order_amount"))
            if abs(row_amount - target_amount) < 0.01:
                score += 25
            elif abs(row_amount - target_amount) <= 5:
                score += 10
            row_date = parse_date_only(row.get("order_date", ""))
            if target_date and row_date and row_date == target_date:
                score += 20
            if score > best_score:
                best_score = score
                best_row = row

        if best_score >= 45 and best_row:
            return best_row, "heuristic_match", best_score
        return {}, "no_match", 0

    raw, match_mode, match_score = resolve_order_row()
    customer_id = raw.get("customer_id", "")
    same_customer_orders = [o for o in orders if customer_id and o.get("customer_id") == customer_id]
    same_customer_orders.sort(key=lambda r: _parse_any_datetime(r.get("order_date", "")) or datetime.min)

    tx_dt = _parse_any_datetime(case.get("transaction_date", "")) or datetime.utcnow()
    tx_date = tx_dt.date()

    previous_order = None
    for row in same_customer_orders:
        row_dt = _parse_any_datetime(row.get("order_date", ""))
        if row_dt and row_dt.date() < tx_date:
            previous_order = row
    if previous_order is None and same_customer_orders:
        previous_order = same_customer_orders[0]

    customer_name = case.get("cardholder", "Not available in source record")
    customer_email = raw.get("customer_email", "Not available in source record")
    customer_phone = raw.get("customer_phone", "Not available in source record")
    customer_ip = raw.get("customer_ip", "Not available in source record")
    customer_city = raw.get("customer_city", "N/A")
    customer_state = raw.get("customer_state", "N/A")
    customer_country = case.get("issuer_country", "United States") or "United States"

    card_bin = raw.get("card_bin", case.get("card_bin", "Not available in source record"))
    auth_code = case.get("auth_code", "Not available in source record")
    device_id = raw.get("device_id", case.get("device_id", "Not available in source record"))

    first_order_dt = _parse_any_datetime(same_customer_orders[0].get("order_date", "")) if same_customer_orders else None
    account_created = first_order_dt or _parse_any_datetime(raw.get("order_date", ""))
    card_binding = _parse_any_datetime(raw.get("order_date", ""))

    prev_amount = _safe_float(previous_order.get("order_amount")) if previous_order else None
    prev_fallback_dt = "Not available in source record"
    prev_date_text = (
        fmt_human_datetime(previous_order.get("order_date", ""), fallback=prev_fallback_dt)
        if previous_order
        else prev_fallback_dt
    )
    prev_ip = previous_order.get("customer_ip", "Not available in source record") if previous_order else "Not available in source record"

    dispute_amount = _safe_float(case.get("amount", 0))
    refunded_amount = _safe_float(case.get("amount_settled", dispute_amount)) or dispute_amount
    reason_line = f"{case.get('reason_code', '')} - {reason.get('title', '')}".strip(" -")
    tx_ref = case.get("payment_psp_ref", "") or case.get("dispute_psp_ref", "")
    dispute_case_ref = case.get("dispute_psp_ref", case.get("case_id", ""))

    from chargeback.engines.cover_letter import build_cover_letter, build_evidence_list
    cover_letter = build_cover_letter(case, raw)
    dynamic_evidence = build_evidence_list(case, raw)

    doc = {
        "generated_at": datetime.now().strftime("%m/%d/%y, %I:%M %p").lstrip("0").replace(" 0", " "),
        "document_title": f"Rebuttal Document - {case.get('case_id', '')}",
        "heading": cover_letter["heading"],
        "subheading": cover_letter["subheading"],
        "cover_letter": cover_letter,
        "dynamic_evidence": dynamic_evidence,
        "summary": {
            "dispute_case_id": dispute_case_ref,
            "original_charge_date": fmt_human_datetime(case.get("transaction_date", ""), fallback=case.get("transaction_date", "")),
            "disputed_amount": dispute_amount,
            "reason_code_line": reason_line,
            "cardholder_name": customer_name,
            "card_brand_last4": f"{case.get('payment_method', 'Card')} ending in {case.get('card_last_four', '----')}",
            "merchant_identity": case.get("merchant", "Merchant"),
            "arn_number": case.get("acquirer_ref", "N/A"),
            "refund_processing_date": fmt_human_date(case.get("submission_date", ""), fallback=case.get("submission_date", case.get("dispute_creation_date", ""))),
            "refunded_amount": refunded_amount,
        },
        "statement": {
            "transaction_ref": tx_ref,
            "arn_number": case.get("acquirer_ref", "N/A"),
            "avs": case.get("avs_response", "N/A"),
            "cvv": case.get("cvv_response", "N/A"),
            "threed_secure": case.get("threed_secure", "N/A"),
        },
        "evidence": {
            "refund_rows": [
                {
                    "exhibit": "Exhibit A-1",
                    "type": "Gateway Refund Receipt Screenshot",
                    "purpose": (
                        f"Proves our merchant terminal successfully issued a formal reversal command for the amount of ${refunded_amount:.2f} "
                        f"on {fmt_human_date(case.get('submission_date', ''), fallback='recorded settlement date')}."
                    ),
                },
                {
                    "exhibit": "Exhibit A-2",
                    "type": "Settlement Log & ARN Metadata",
                    "purpose": (
                        f"Displays the raw transactional metadata confirming generation of "
                        f"ARN: {case.get('acquirer_ref', 'N/A')}. "
                        "This acts as federal/banking tracking confirmation that the credit has passed from our acquirer to the "
                        "customer's card network."
                    ),
                },
            ],
            "security_rows": [
                {
                    "exhibit": "Exhibit B",
                    "type": "AVS & CVV Authentication Log",
                    "details": (
                        f"AVS Response: {case.get('avs_response', 'N/A')}\n"
                        f"CVV Response: {case.get('cvv_response', 'N/A')}\n\n"
                        "Proves the user possessed the true billing credentials."
                    ),
                },
                {
                    "exhibit": "Exhibit C",
                    "type": "Digital Footprint / IP Address Data",
                    "details": (
                        f"IP Address recorded at checkout with geolocation "
                        f"data, aligning directly with the cardholder's known "
                        f"billing jurisdiction ({customer_country})."
                    ),
                },
                {
                    "exhibit": "Exhibit D",
                    "type": "Proof of Fulfillment / Delivery",
                    "details": (
                        "Carrier details, signed delivery confirmation, or "
                        "digital logs confirming that the order was actively "
                        "received or downloaded by the customer."
                    ),
                },
            ],
        },
        "customer": {
            "name": customer_name,
            "email": customer_email,
            "phone": customer_phone,
            "city": customer_city,
            "state": customer_state,
            "country": customer_country,
            "ip_address": customer_ip,
            "ip_country": "US" if "united states" in customer_country.lower() else customer_country,
            "device_id": device_id,
            "account_created": account_created.strftime("%Y-%m-%dT%H:%M:%SZ") if account_created else "Not available in source record",
            "card_binding_time": card_binding.strftime("%Y-%m-%dT%H:%M:%SZ") if card_binding else "Not available in source record",
            "card_bin": card_bin,
            "card_last_four": case.get("card_last_four", "----"),
            "bin_country": "US" if "united states" in customer_country.lower() else customer_country,
        },
        "payment_history": {
            "txn1_date": prev_date_text,
            "txn1_amount": prev_amount,
            "txn1_email": previous_order.get("customer_email", customer_email) if previous_order else customer_email,
            "txn1_card_bin": card_bin,
            "txn1_last4": previous_order.get("card_last_four", case.get("card_last_four", "----")) if previous_order else case.get("card_last_four", "----"),
            "txn1_bin_country": "US" if "united states" in customer_country.lower() else customer_country,
            "txn1_ip_country": "US" if "united states" in customer_country.lower() else customer_country,
            "txn1_ip_address": prev_ip,
            "txn2_date": fmt_human_datetime(case.get("transaction_date", ""), fallback=case.get("transaction_date", "")),
            "txn2_amount": dispute_amount,
            "txn2_email": customer_email,
            "txn2_card_bin": card_bin,
            "txn2_last4": case.get("card_last_four", "----"),
            "txn2_bin_country": "US" if "united states" in customer_country.lower() else customer_country,
            "txn2_ip_country": "US" if "united states" in customer_country.lower() else customer_country,
            "txn2_ip_address": customer_ip,
            "device_id": device_id,
        },
        "order": {
            "match_mode": match_mode,
            "match_score": match_score,
            "order_id": case.get("order_id", ""),
            "product_name": raw.get("product_name", "Program / Service Access"),
            "product_id": raw.get("product_id", f"SVC-{case.get('case_id', '0000')}"),
            "quantity": safe_int(raw.get("quantity", 1), 1),
            "price": _safe_float(raw.get("unit_price", dispute_amount)),
            "purchase_amount": _safe_float(raw.get("order_amount", dispute_amount)),
            "order_date": fmt_human_datetime(case.get("transaction_date", ""), fallback=case.get("transaction_date", "")),
            "delivery_option": "STANDARD",
            "ship_from": raw.get("ship_from_warehouse", "FC-N/A"),
            "shop_name": case.get("descriptor_name", case.get("merchant", "N/A")),
            "delivery_status": "NORMAL" if str(raw.get("fulfillment_status", "")).lower() == "delivered" else raw.get("fulfillment_status", "Disputed Status"),
            "delivery_event": (
                "Package has been delivered!"
                if str(raw.get("fulfillment_status", "")).lower() == "delivered"
                else "Delivery status available in tracking record."
            ),
            "delivery_time": raw.get("delivery_date", case.get("submission_date", "N/A")),
            "delivery_title": raw.get("fulfillment_status", "Status Pending"),
            "shipment_provider": raw.get("shipping_carrier", "N/A"),
            "tracking_number": raw.get("tracking_number", "N/A"),
        },
        "payment": {
            "merchant_name": case.get("merchant", "N/A"),
            "merchant_id": case.get("merchant_account", "N/A"),
            "currency": "USD",
            "transaction_amount": refunded_amount,
            "transaction_date": fmt_human_datetime(case.get("transaction_date", ""), fallback=case.get("transaction_date", "")),
            "authorization_code": auth_code,
            "threed_secure": case.get("threed_secure", "N/A"),
            "order_id": case.get("order_id", "N/A"),
        },
        "support_email": f"support@{MERCHANT_CONFIG['descriptor_url'] or 'merchant.example.com'}",
        "page_total": 4,
    }

    return render_template("rebuttal.html", case=case, reason=reason, auto_print=auto_print, doc=doc)


@app.route("/add-case", methods=["GET", "POST"])
def add_case():
    if request.method == "GET":
        return render_template("add_case.html")

    form = request.form
    case_id = form.get("case_id", "NEW-001")
    amount = float(form.get("amount", 0))
    reason_code = form.get("reason_code", "13.1")
    h = int(hashlib.md5(case_id.encode()).hexdigest(), 16)

    category_map = {
        "Fraud": "Fraud - Card Not Present (CNP)",
        "Merchandise": "Merchandise - Item Not Received",
        "Processing": "Processing - Incorrect Amount",
        "Subscription": "Subscription - Cancelled Recurring",
        "Refund": "Refund - Credit Not Processed",
    }
    category = form.get("category", "Fraud")
    scenario = category_map.get(category, "Fraud - Card Not Present (CNP)")

    new_case = {
        "case_id": case_id,
        "scenario": scenario,
        "chargeback_category": category,
        "reason_code": reason_code,
        "processor": form.get("processor", "Adyen"),
        "amount": amount,
        "win_probability": 50,
        "submission_date": form.get("chargeback_date", ""),
        "submission_status": "Pending",
        "outcome": "Pending",
        "merchant": MERCHANT_CONFIG["company_name"],
        "merchant_account": form.get("merchant_id", MERCHANT_CONFIG["merchant_account_number"]),
        "descriptor_name": MERCHANT_CONFIG["dba_name"],
        "descriptor_url": MERCHANT_CONFIG["descriptor_url"],
        "payment_method": form.get("card_type", "Visa"),
        "card_last_four": form.get("card_last_four", f"{h % 10000:04d}"),
        "card_expiry": form.get("card_expiry", "12/2028"),
        "cardholder": form.get("customer_name", "***REDACTED***"),
        "issuer_country": form.get("country", "United States"),
        "issuer_name": form.get("state", ""),
        "avs_response": "Both postal code and address match (Y)" if form.get("avs_cvv") == "Pass" else "No match",
        "cvv_response": "Supplied, Matches (M)" if form.get("avs_cvv") == "Pass" else "Not provided",
        "threed_secure": "Authenticated" if form.get("avs_cvv") == "Pass" else "Not Offered",
        "transaction_date": form.get("transaction_date", ""),
        "amount_authorized": amount,
        "amount_settled": amount,
        "dispute_psp_ref": case_id,
        "payment_psp_ref": form.get("transaction_id", ""),
        "dispute_creation_date": form.get("chargeback_date", ""),
        "order_id": form.get("order_id", f"ORD-{case_id}"),
        "acquirer_ref": form.get("arn", f"{24793306171002500000000 + h % 999999}"),
        "acquirer_code": form.get("merchant_id", ""),
        "auto_defended": form.get("action", "") == "save_and_represent",
        "liability_shift": form.get("avs_cvv") == "Pass",
        "issuer_comments": form.get("notes", ""),
        "dispute_history": [
            {"event": "CaseCreated", "date": form.get("chargeback_date", "")},
            {"event": "ManualEntry", "date": "Now"},
        ],
    }
    CASES.append(new_case)
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/case/<case_id>/accept", methods=["POST"])
def accept_case(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return jsonify({"status": "error"}), 404
    case["ml_override"] = "accept_refund"
    case["outcome"] = "Accepted"
    case["submission_status"] = "Accepted"
    return jsonify({"status": "ok", "case_id": case_id})


@app.route("/gateway-receipt/<case_id>")
def gateway_receipt(case_id):
    case = next((c for c in CASES if c["case_id"] == case_id), None)
    if not case:
        return "Case not found", 404
    h = int(hashlib.md5(case_id.encode()).hexdigest(), 16)
    card_type = case.get("payment_method", "Visa")
    last4 = case.get("card_last_four", "0000")
    bin_map = {"Visa": "411111", "VISA": "411111", "Mastercard": "520000", "MASTERCARD": "520000",
               "Amex": "371449", "AMEX": "371449", "Discover": "601100", "DISCOVER": "601100",
               "Klarna": "540200", "KLARNA": "540200"}
    receipt = {
        "merchant_name": case.get("merchant") or MERCHANT_CONFIG["company_name"],
        "transaction_id": case.get("payment_psp_ref", "") or case.get("dispute_psp_ref", case_id),
        "transaction_date": case.get("transaction_date", "N/A"),
        "amount": case.get("amount_authorized", case.get("amount", 0)),
        "transaction_type": "Card Void" if case.get("refund_type") else "Sale",
        "entry_method": "Keyed",
        "cc_number": f"{bin_map.get(card_type, '400000')}******{last4}",
        "cc_expiration": case.get("card_expiry", "") or "XX/XX",
        "cc_type": card_type,
        "avs_status": case.get("avs_response", "N/A"),
        "cvv_status": case.get("cvv_response", "N/A"),
        "auth_code": f"{h % 999999:06d}",
        "processor": case.get("processor", "Unknown"),
        "currency": case.get("currency", "USD"),
    }
    return render_template("gateway_receipt.html", case=case, receipt=receipt)


# ─── API Routes ────────────────────────────────────────────────────────────────

@app.route("/api/cases")
def api_cases():
    scenario = request.args.get("scenario", "All")
    processor = request.args.get("processor", "All")
    outcome = request.args.get("outcome", "All")

    filtered = CASES
    if scenario != "All":
        filtered = [c for c in filtered if c["scenario"] == scenario]
    if processor != "All":
        filtered = [c for c in filtered if c["processor"] == processor]
    if outcome != "All":
        filtered = [c for c in filtered if c["outcome"] == outcome]

    return jsonify(filtered)



# Load the working data set at import time so every worker/reload has the full
# queue, not just the seeded demo cases.
_STARTUP_LOADED = _load_startup_cases()

if __name__ == "__main__":
    print(f"Loaded {_STARTUP_LOADED or len(CASES)} cases from "
          f"{STARTUP_DATASET if _STARTUP_LOADED else 'seed data'}")
    app.run(debug=True, port=8000)
