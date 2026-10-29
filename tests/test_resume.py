"""Resume commands come from a validated {argv, cwd} spec and are only rendered, never executed."""
import shlex

from bitacora.model import Run, Session
from bitacora.providers.base import Capabilities, Provider, render_resume, resume_cwd, resume_value
from bitacora.providers.claude import ClaudeProvider
from bitacora.providers.codex import CodexProvider

SID = "0199a1b2-0000-7000-8000-000000000001"


def session(tmp_path, **attrs) -> Session:
    s = Session(tmp_path / "x.jsonl")
    for name, value in attrs.items():
        setattr(s, name, value)
    return s


def run(tmp_path, provider, **attrs) -> Run:
    return Run("k", provider, session(tmp_path, **attrs), None, "x")


def test_resume_value_accepts_identifiers_and_rejects_shell_syntax():
    assert resume_value(SID) == SID
    assert resume_value("gpt-6.1_mini:fast@prod/eu+1") == "gpt-6.1_mini:fast@prod/eu+1"
    assert resume_value("a" * 128) == "a" * 128
    assert resume_value("a" * 129) is None
    for bad in ("a b", "a;b", "a'b", "-x", "", None):
        assert resume_value(bad) is None


def test_resume_cwd_rejects_control_characters_and_oversized_values():
    assert resume_cwd("/repo") == "/repo"
    assert resume_cwd("C:\\a b\\it's") == "C:\\a b\\it's"
    assert resume_cwd("a\nb") is None
    assert resume_cwd("a\x7fb") is None
    assert resume_cwd("") is None
    assert resume_cwd(None) is None
    assert resume_cwd("/" + "x" * 4095) == "/" + "x" * 4095
    assert resume_cwd("/" + "x" * 4096) is None


def test_hostile_session_id_drops_the_whole_resume(tmp_path):
    for bad in ("a b", "a;b", "a'b", "-x", "a$(reboot)", "a`b`"):
        for provider in (CodexProvider(), ClaudeProvider()):
            assert provider.resume(run(tmp_path, provider.name, session_id=bad, cwd="/repo")) is None
            assert provider.resume_cmd(run(tmp_path, provider.name, session_id=bad, cwd="/repo")) is None


def test_unvalidated_session_fields_never_reach_the_argv(tmp_path):
    s = session(tmp_path, session_id=SID, cwd="/repo", headless=True,
                model="gpt; reboot", effort="a b", sandbox="$(whoami)")
    spec = CodexProvider().resume(Run("k", "codex", s, None, "x"))
    assert spec == {"argv": ["codex", "exec", "resume", SID, "-"], "cwd": "/repo"}
    command = render_resume(spec, "sh")
    assert "reboot" not in command and "whoami" not in command


def test_apostrophe_in_cwd_is_escaped_in_both_shells(tmp_path):
    cwd = "C:\\it's a repo"
    spec = ClaudeProvider().resume(run(tmp_path, "claude", session_id=SID, cwd=cwd))
    assert render_resume(spec, "powershell") == (
        "Set-Location -LiteralPath 'C:\\it''s a repo'; claude --resume " + SID)
    rendered = render_resume(spec, "sh")
    assert rendered == f"cd -- {shlex.quote(cwd)} && claude --resume {SID}"
    assert "'\"'\"'" in rendered  # shlex closes the quote, escapes ' and reopens it


def test_control_character_in_cwd_omits_the_cd_but_keeps_the_argv(tmp_path):
    for bad_cwd in ("/repo\nrm -rf /", "/repo\x00", "/repo\x7f"):
        spec = ClaudeProvider().resume(run(tmp_path, "claude", session_id=SID, cwd=bad_cwd))
        assert spec == {"argv": ["claude", "--resume", SID], "cwd": None}
        for shell in ("sh", "powershell"):
            assert render_resume(spec, shell) == f"claude --resume {SID}"


def test_no_cwd_means_no_directory_prefix(tmp_path):
    sh_cmd = ClaudeProvider().resume_cmd(run(tmp_path, "claude", session_id=SID, cwd=None), "sh")
    assert sh_cmd == f"claude --resume {SID}" and "cd" not in sh_cmd
    ps_cmd = ClaudeProvider().resume_cmd(run(tmp_path, "claude", session_id=SID, cwd=None), "powershell")
    assert ps_cmd == f"claude --resume {SID}" and "Set-Location" not in ps_cmd


def test_arguments_are_quoted_only_when_needed():
    simple = {"argv": ["codex", "resume", SID, "-m", "gpt-6.1"], "cwd": None}
    assert render_resume(simple, "powershell") == f"codex resume {SID} -m gpt-6.1"
    for value, quoted in (("two words", "'two words'"), ('a "b"', "'a \"b\"'"), ("it's", "'it''s'")):
        spec = {"argv": ["codex", "resume", SID, value], "cwd": None}
        assert render_resume(spec, "powershell") == f"codex resume {SID} {quoted}"
        assert render_resume(spec, "sh") == shlex.join(["codex", "resume", SID, value])


def test_resume_capability_gates_the_command(tmp_path):
    class FakeProvider(Provider):
        name = "fake"

        def resume(self, _run):
            return {"argv": ["fake", "resume", SID], "cwd": "/repo"}

    r = run(tmp_path, "fake", session_id=SID)
    enabled = FakeProvider()
    assert enabled.capabilities.resume
    assert enabled.resume_cmd(r, "sh") == render_resume(enabled.resume(r), "sh")

    disabled = FakeProvider()
    disabled.capabilities = Capabilities(resume=False)
    assert disabled.resume(r) is not None  # the spec is valid; only the capability says no command
    assert disabled.resume_cmd(r) is None
    assert disabled.resume_cmd(r, "sh") is None


def test_default_shell_follows_the_platform(monkeypatch):
    import bitacora.providers.base as base
    spec = {"argv": ["claude", "--resume", SID], "cwd": "/repo"}
    monkeypatch.setattr(base.os, "name", "posix")
    assert render_resume(spec) == f"cd -- /repo && claude --resume {SID}"
    monkeypatch.setattr(base.os, "name", "nt")
    assert render_resume(spec) == f"Set-Location -LiteralPath '/repo'; claude --resume {SID}"
