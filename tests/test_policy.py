"""Security policy tests (PRD §§36-37): gates, code AST, redaction, enforcement."""
import json

import pytest

from agent.actions import Action
from agent.computer import Computer
from agent.executor import PersistentExecutor
from agent.model import SYSTEM_PROMPT
from agent.policy import PolicyViolation, SecurityPolicy
from agent.sessions import SessionStore
from sandbox.mock import MockSandbox


def test_prompt_declares_untrusted_content():
    assert "UNTRUSTED" in SYSTEM_PROMPT


def test_normal_actions_allowed():
    p = SecurityPolicy()
    assert p.check_action(Action(type="click", x=1, y=1)) == "allow"
    assert p.check_action(Action(type="type", text="hello")) == "allow"
    assert p.check_action(Action(type="press", key="Enter")) == "allow"
    assert p.check_action(Action(type="goto", text="https://example.com")) == "allow"


def test_disallowed_action_denied():
    p = SecurityPolicy(allowed_actions={"click", "done"})
    with pytest.raises(PolicyViolation, match="not allowed"):
        p.check_action(Action(type="type", text="x"))


def test_destructive_combo_needs_confirmation():
    p = SecurityPolicy()
    verdict = p.check_action(Action(type="press", key="alt+F4"))
    assert verdict.startswith("confirm:")
    with pytest.raises(PolicyViolation, match="confirmation required"):
        p.enforce(Action(type="press", key="alt+F4"))
    p2 = SecurityPolicy(auto_confirm_destructive=True)
    assert p2.check_action(Action(type="press", key="ctrl+q")) == "allow"


def test_domain_allowlist():
    p = SecurityPolicy(allowed_domains=["example.com"])
    assert p.check_action(Action(type="goto", text="https://example.com/x")) == "allow"
    assert p.check_action(Action(type="goto", text="https://sub.example.com/")) == "allow"
    with pytest.raises(PolicyViolation, match="domain not allowed"):
        p.check_action(Action(type="goto", text="https://evil.test/"))
    assert SecurityPolicy.from_env().allowed_domains is None


def test_code_ast_gate():
    p = SecurityPolicy()
    p.check_code("import math\n_ = math.floor(3.7)")
    p.check_code("computer.click(1, 2)\nprint('hi')")
    for bad in ("import os", "import subprocess as sp", "from socket import socket",
                "x = open('/etc/passwd').read()", "eval('1')",
                "breed = __import__('os')", "import os; os.system('id')"):
        with pytest.raises(PolicyViolation):
            p.check_code(bad)
    with pytest.raises(PolicyViolation):
        p.check_code("def broken(:")
    with pytest.raises(PolicyViolation, match="destructive shell"):
        p.check_code("cmd = 'rm -rf /tmp/x'")


def test_redaction():
    p = SecurityPolicy()
    assert "***REDACTED***" in p.redact("key=gsk_abcdef1234567890 ok")
    assert "hello" in p.redact("hello world")
    assert "***REDACTED***" in p.redact("tok sk-abcdefgh12345678 end")
    ev = SecurityPolicy.redact_event({"kind": "act", "detail": "typed gsk_zzz123456789"})
    assert "gsk_zzz" not in json.dumps(ev)


def test_session_log_redacts_env_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "supersecrettoken123")
    st = SessionStore(tmp_path, "s")
    st.write_meta("t", "m")
    ev = st.log_event({"kind": "act", "detail": "used supersecrettoken123 here"})
    assert "supersecrettoken123" not in json.dumps(ev)
    on_disk = (tmp_path / "s" / "events.jsonl").read_text()
    assert "supersecrettoken123" not in on_disk


async def test_computer_enforces_policy():
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    comp.attach_policy(SecurityPolicy())
    await comp.click(1, 1)  # allowed passes through
    with pytest.raises(PolicyViolation, match="confirmation required"):
        await comp.press("alt+F4")
    assert len(m.actions) == 1  # denied action never touched the backend
    with pytest.raises(PolicyViolation, match="blocked import"):
        ex = PersistentExecutor()
        comp.attach_executor(ex)
        await comp.execute_python("import os")
        ex.close()


async def test_injection_cannot_escalate():
    """Screen says 'press alt+f4' -> model obeys -> app still denies (§37).

    Permissions live in code, not prose: even a fully jailbroken model choice
    dies at enforce() before any effect.
    """
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    policy = SecurityPolicy()
    comp.attach_policy(policy)
    # the "compromised" model decision:
    evil = Action(type="press", key="alt+F4")
    with pytest.raises(PolicyViolation):
        await comp.execute(evil)
    assert m.actions == []


def test_audit_event(tmp_path):
    st = SessionStore(tmp_path, "s")
    st.write_meta("t", "m")
    SecurityPolicy().audit(st, "deny", "action not allowed: spawn")
    lines = (tmp_path / "s" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["kind"] == "audit" and "deny" in ev["detail"]
