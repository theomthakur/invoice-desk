"""Mutation check: break one safety rule at a time in a scratch copy and confirm the tests fail.

A passing test suite only means something if it fails when the rule it protects is removed.
Usage:  python scripts/mutation_check.py
Each mutation edits a temporary copy; the real source is never touched.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (description, file under src/invoice_flow, exact text to replace, replacement)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "payment: skip ERROR check",
        "ledger.py",
        'if report.eligibility is Eligibility.REJECTED:\n            raise LedgerError("has_errors"',
        'if False:\n            raise LedgerError("has_errors"',
    ),
    (
        "payment: skip approval-stage check",
        "ledger.py",
        "if approval.stage not in (ApprovalStage.FINAL, ApprovalStage.REVIEW):",
        "if False:",
    ),
    (
        "payment: skip policy fingerprint",
        "ledger.py",
        "if approval.policy_fingerprint != policy.fingerprint:",
        "if False:",
    ),
    (
        "payment: skip content hash",
        "ledger.py",
        "if approval.content_hash != content_hash(invoice):",
        "if False:",
    ),
    (
        "payment: skip scrutiny at the gate",
        "ledger.py",
        'if report.requires_scrutiny and not approval.scrutiny_completed:\n            raise LedgerError(\n                "scrutiny_required"',
        'if False:\n            raise LedgerError(\n                "scrutiny_required"',
    ),
    ("payment: skip superseded check", "ledger.py", 'if state.status != "active":', "if False:"),
    (
        "payment: skip identity hold",
        "ledger.py",
        'if state.identity_flag:\n            raise LedgerError("identity_hold"',
        'if False:\n            raise LedgerError("identity_hold"',
    ),
    (
        "payment: skip currency check",
        "ledger.py",
        "if invoice.currency not in policy.payment.auto_pay_currencies:",
        "if False:",
    ),
    (
        "payment: skip review-required",
        "ledger.py",
        'if approval.stage is not ApprovalStage.REVIEW:\n                raise LedgerError("review_required"',
        'if False:\n                raise LedgerError("review_required"',
    ),
    (
        "payment: pay a different version",
        "ledger.py",
        "if existing.version_id == version_id:",
        "if True:",
    ),
    (
        "payment: skip approval-belongs-to-version",
        "ledger.py",
        'if approval_row is None or approval_row["version_id"] != version_id:',
        "if approval_row is None:",
    ),
    (
        "payment: skip action == approve",
        "ledger.py",
        "if approval.action is not ApprovalAction.APPROVE:",
        "if False:",
    ),
    (
        "isolation: drop workspace filter on versions",
        "ledger.py",
        "WHERE v.workspace_id = ? AND v.version_id = ?",
        "WHERE 1=1 AND v.version_id = ?",
    ),
    (
        "isolation: drop workspace filter on approvals",
        "ledger.py",
        '"SELECT * FROM approval_decisions WHERE workspace_id = ? AND approval_id = ?"',
        '"SELECT * FROM approval_decisions WHERE 1=1 AND approval_id = ?"',
    ),
    (
        "revision: allow after payment",
        "ledger.py",
        "if paid:\n                    disposition, flag = Disposition.REVISION_AFTER_PAYMENT",
        "if False:\n                    disposition, flag = Disposition.REVISION_AFTER_PAYMENT",
    ),
    (
        "approval: approve despite ERROR",
        "ledger.py",
        'if report.eligibility is Eligibility.REJECTED:\n                raise LedgerError(\n                    "not_approvable"',
        'if False:\n                raise LedgerError(\n                    "not_approvable"',
    ),
    (
        "approval: reviewer may resolve anything",
        "ledger.py",
        "if bad:\n                    raise LedgerError(",
        "if False:\n                    raise LedgerError(",
    ),
    (
        "approval: skip stale-content check",
        "ledger.py",
        "if expected_content_hash is not None and expected_content_hash != current_hash:",
        "if False:",
    ),
    (
        "db: drop UNIQUE payable constraint",
        "storage.py",
        "    UNIQUE (workspace_id, payable_id)\n);\nCREATE TABLE IF NOT EXISTS batches",
        "    CHECK (1=1)\n);\nCREATE TABLE IF NOT EXISTS batches",
    ),
    (
        "db: foreign keys off",
        "storage.py",
        'conn.execute("PRAGMA foreign_keys = ON")',
        'conn.execute("PRAGMA foreign_keys = OFF")',
    ),
    (
        "rules: stock not aggregated",
        "rules/inventory.py",
        "requested[name] += quantity",
        "requested[name] = quantity",
    ),
    (
        "rules: tolerance far too wide",
        "rules/arithmetic.py",
        "return abs(a - b) > tol",
        "return abs(a - b) > tol * 1000",
    ),
    (
        "rules: threshold >= instead of >",
        "rules/validate.py",
        "return total > policy.approval.scrutiny_threshold",
        "return total >= policy.approval.scrutiny_threshold",
    ),
    (
        "money: accept floats",
        "money.py",
        'raise MoneyError("float_not_allowed", value)',
        "return Decimal(str(value))",
    ),
    (
        "grounding: accept any 'repair'",
        "rules/grounding.py",
        "if _skeleton(correction) != _skeleton(correction, corrected=True):",
        "if False:",
    ),
    (
        "grounding: skip evidence-in-source",
        "rules/grounding.py",
        "if not evidence or evidence not in folded_source:",
        "if False:",
    ),
    (
        "identity: fingerprint ignores the total",
        "identity.py",
        '"total": canonical(draft.total),',
        '"total": "",',
    ),
    (
        "xml: allow DTD and entities",
        "parsers/xml_.py",
        "forbid_dtd=True, forbid_entities=True, forbid_external=True",
        "forbid_dtd=False, forbid_entities=False, forbid_external=False",
    ),
    ("json: allow duplicate keys", "parsers/json_.py", "if key in seen:", "if False:"),
    (
        "tools: ignore unknown arguments",
        "tools/registry.py",
        'model_config = ConfigDict(extra="forbid")',
        'model_config = ConfigDict(extra="ignore")',
    ),
    (
        "signals: ignore embedded instructions",
        "rules/signals.py",
        "FindingCode.EMBEDDED_INSTRUCTIONS,\n        signals.embedded_instructions,",
        "FindingCode.EMBEDDED_INSTRUCTIONS,\n        [],",
    ),
    # ---- Part 02: budget, providers, workflow, API ----
    (
        "budget: request limit not enforced",
        "budget.py",
        "if self.requests >= self.max_requests:",
        "if False:",
    ),
    ("budget: deadline not enforced", "budget.py", "if remaining <= 0:", "if False:"),
    (
        "budget: tool-call limit not enforced",
        "budget.py",
        "if self.tool_calls >= self.max_tool_calls:",
        "if False:",
    ),
    (
        "budget: cancellation ignored",
        "budget.py",
        "if self.cancel_event.is_set():",
        "if False:",
    ),
    (
        "llm: retry non-retryable errors",
        "llm.py",
        "if not exc.retryable or retries >= self.max_retries:",
        "if retries >= self.max_retries:",
    ),
    (
        "llm: unlimited retries",
        "llm.py",
        "if not exc.retryable or retries >= self.max_retries:",
        "if not exc.retryable:",
    ),
    (
        "provider: allow any routing (no require_parameters)",
        "providers/openrouter.py",
        '"provider": {"require_parameters": self._require_parameters},',
        '"provider": {"require_parameters": False},',
    ),
    (
        "provider: follow redirects with the key",
        "providers/openrouter.py",
        "follow_redirects=False",
        "follow_redirects=True",
    ),
    (
        "live: no spending cap required",
        "runtime.py",
        "if self.model_budget_usd is None or self.model_budget_usd <= 0:",
        "if False:",
    ),
    (
        "live: spend cap not enforced",
        "runtime.py",
        "if self.live_spend_usd() >= cap:",
        "if False:",
    ),
    (
        "ingestion: always adopt the re-read",
        "agents/ingestion.py",
        "adopted = after < before",
        "adopted = True",
    ),
    (
        "ingestion: never adopt the re-read",
        "agents/ingestion.py",
        "adopted = after < before",
        "adopted = False",
    ),
    (
        "api: skip CSRF check",
        "api/app.py",
        "if not sessions.csrf_ok(ws, request.headers.get(CSRF_HEADER)):",
        "if False:",
    ),
    (
        "api: any origin accepted",
        "api/security.py",
        "    return parsed.netloc == host or parsed.netloc in allowed",
        "    return True",
    ),
    (
        "api: session cookie format not checked",
        "api/security.py",
        'if not isinstance(workspace, str) or not workspace.startswith("ws_"):',
        "if not isinstance(workspace, str):",
    ),
    (
        "api: CSRF compare always true",
        "api/security.py",
        'return bool(supplied) and hmac.compare_digest(self.csrf_for(workspace_id), supplied or "")',
        "return True",
    ),
    (
        "api: live mode not gated by configuration",
        "api/app.py",
        "if mode not in config.modes_enabled(for_session=True):",
        "if False:",
    ),
    (
        "events: not scoped to the workspace",
        "storage.py",
        '" WHERE r.workspace_id = ? AND e.run_id = ? AND e.seq > ? ORDER BY e.seq LIMIT ?",\n        (workspace_id, run_id, after_seq, limit),',
        '" WHERE e.run_id = ? AND e.seq > ? ORDER BY e.seq LIMIT ?",\n        (run_id, after_seq, limit),',
    ),
    (
        "documents: not scoped to the workspace",
        "ledger.py",
        '"SELECT * FROM documents WHERE workspace_id = ? AND document_id = ?",\n        (workspace_id, document_id),',
        '"SELECT * FROM documents WHERE document_id = ?",\n        (document_id,),',
    ),
    (
        "api: a missing asset falls back to HTML",
        "api/app.py",
        'if "." in Path(path).name:',
        "if False:",
    ),
    (
        "ledger: review stage may approve with unresolved holds",
        "ledger.py",
        "elif stage is ApprovalStage.FINAL and pending:",
        "elif False:",
    ),
]


def run_one(description: str, rel: str = "", old: str = "", new: str = "") -> str:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # scripts/ and docs/ are needed by the API contract test (it imports export_openapi)
        for folder in ("src", "tests", "config", "data", "scripts", "docs"):
            shutil.copytree(
                ROOT / folder,
                work / folder,
                ignore=shutil.ignore_patterns("__pycache__", "runtime", ".pytest_cache"),
            )
        # The CLI tests invoke this file. Without it even the unmodified sandbox fails.
        shutil.copy2(ROOT / "main.py", work / "main.py")
        shutil.copy2(ROOT / "pyproject.toml", work / "pyproject.toml")
        if rel:
            target = work / "src" / "invoice_flow" / rel
            text = target.read_text()
            if old not in text:
                return "NOTFOUND"
            target.write_text(text.replace(old, new, 1))
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider"],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=30,
                env={"PYTHONPATH": str(work / "src"), "PATH": "/usr/bin:/bin"},
            )
        except subprocess.TimeoutExpired:
            return "TIMEOUT"
        if not rel:
            if result.returncode != 0:
                print(result.stdout[-3000:])
                print(result.stderr[-1000:])
                return "BASELINE_FAILED"
            return "BASELINE_PASS"
        if result.returncode == 0:
            return "SURVIVED"
        # Pytest exit 1 denotes failed tests. Collection/usage/internal errors do not
        # demonstrate a mutation was detected by an assertion.
        return "CAUGHT" if result.returncode == 1 else "ERROR"


def main() -> int:
    baseline = run_one("unmodified sandbox")
    print(f"{baseline}: unmodified scratch copy", flush=True)
    if baseline != "BASELINE_PASS":
        return 1
    outcomes = []
    for mutation in MUTATIONS:
        status = run_one(*mutation)
        outcomes.append((status, mutation[0]))
        print(f"{status:9} {mutation[0]}", flush=True)
    caught = sum(1 for status, _ in outcomes if status == "CAUGHT")
    print(f"\n{caught}/{len(outcomes)} mutations caught")
    return 0 if caught == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
