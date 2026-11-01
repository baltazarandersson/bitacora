"""Conformance checks every agent provider must pass; new providers run the same kit."""
from __future__ import annotations

import json
import time
from pathlib import Path

from bitacora.model import ProcInfo, Run, Session
from bitacora.providers.base import _SUPPORT_FIELDS, Capabilities, Provider, Support, render_resume
from bitacora.sources import READER_STATES, SessionReader, SessionRef

KINDS = {"file", "dir", "sqlite", "composite"}
TIERS = {"verified", "best-effort", "experimental", "unsupported"}
USAGE_BASES = {"processed", "reported", "unknown"}
ORIGIN_KINDS = {"terminal", "background", "desktop", "ide", "subagent", "other"}


def _check_ref(provider: Provider, ref: SessionRef, seen_keys: set[str]) -> None:
    assert isinstance(ref, SessionRef)
    assert ref.provider == provider.name
    assert ref.key and ref.key not in seen_keys  # discovery yields unique references
    seen_keys.add(ref.key)
    assert ref.kind in KINDS
    assert str(ref.locator) not in ref.key  # the key is opaque, never a raw locator or path
    if ref.kind == "file":
        assert isinstance(ref.locator, Path) and ref.locator.exists()


def _check_reader(provider: Provider, ref: SessionRef) -> Session:
    reader = provider.open(ref)
    assert isinstance(reader, SessionReader)
    assert reader.ref == ref
    reader.update()
    session = reader.session()
    assert isinstance(session, Session)
    assert session.locator == ref.locator
    events, messages, tokens = session.events_total, list(session.messages), session.tokens

    # A second update reads nothing new: events, messages and totals stay put.
    reader.update()
    assert session.events_total == events
    assert list(session.messages) == messages
    assert session.tokens == tokens

    checkpoint = reader.checkpoint()
    assert isinstance(checkpoint, dict)
    assert json.loads(json.dumps(checkpoint)) == checkpoint  # JSON-compatible, no paths

    health = reader.health()
    assert health.state in READER_STATES
    assert isinstance(health.records, int) and isinstance(health.parsed, int)
    assert isinstance(health.discarded, int) and isinstance(health.unknown, int)
    assert health.last_good is None or isinstance(health.last_good, (int, float))
    assert str(ref.locator) not in health.detail
    assert health.records >= health.discarded + health.unknown
    return session


def _check_capabilities(provider: Provider, session: Session) -> None:
    caps = provider.capabilities
    if caps.messages is Support.NONE:
        assert not list(session.messages)
    if caps.commands is Support.NONE:
        assert session.n_cmds == 0
    if caps.files is Support.NONE:
        assert not session.files
    if caps.questions is Support.NONE:
        assert not session.open_questions
    if caps.usage is Support.NONE:
        assert session.tokens == 0
    if caps.context is Support.NONE:
        assert session.ctx_window is None
    if caps.titles is Support.NONE:
        # `titles` is the external Provider.title() hook (thread index, rename log, external metadata),
        # not Session.title parsed from the transcript: Claude can parse an in-file title and still
        # declare titles=Support.NONE here.
        assert provider.title(session) is None
    if caps.quota is Support.NONE:
        assert provider.quota([session]) is None
    if caps.resume is Support.NONE:
        assert provider.resume_cmd(Run("kit", provider.name, session, None, "kit")) is None
    if caps.processes:
        assert provider.process_names
    else:
        assert provider.procs([]) == []


def _check_origin(provider: Provider, session: Session) -> None:
    kind = provider.origin_kind(session)
    assert kind in ORIGIN_KINDS
    assert provider.run_meta(session, None).get("origin_kind") == kind


def _check_quota(provider: Provider, session: Session) -> None:
    quota = provider.quota([session])
    if provider.capabilities.quota is Support.NONE:
        assert quota is None
        return
    if quota is None:
        return
    assert isinstance(quota, dict)
    assert quota.get("reporting") in {"windows", "limit-only", "none"}
    assert isinstance(quota.get("windows"), list)
    assert quota.get("limit") is None or isinstance(quota.get("limit"), dict)
    assert json.loads(json.dumps(quota)) == quota


def _check_match(provider: Provider, proc: ProcInfo | None = None) -> None:
    pi = proc or ProcInfo(provider.name, 999_999, time.time(), [provider.name or "synthetic"])
    ref = provider.match_ref(pi, set())
    if proc is not None:
        assert ref is not None  # a live process that really belongs to this provider must find its session
    elif not provider.capabilities.processes:
        assert ref is None  # a provider without process support matches no synthetic process
    if ref is None:
        return
    _check_ref(provider, ref, set())
    path = provider.match(pi, set())
    if ref.kind == "file":
        assert isinstance(path, Path) and ref.locator == path
    else:
        assert path is None


def _check_locator_identity(provider: Provider, refs: list[SessionRef]) -> None:
    groups: dict[str, list[SessionRef]] = {}
    for ref in refs:
        groups.setdefault(str(ref.locator), []).append(ref)
    for group in groups.values():
        if len(group) < 2:
            continue
        assert len({ref.key for ref in group}) == len(group)
        sessions = []
        for ref in group[:2]:
            reader = provider.open(ref)
            reader.update()
            sessions.append(reader.session())
        assert sessions[0] is not sessions[1]
        assert sessions[0].session_id and sessions[1].session_id
        assert sessions[0].session_id != sessions[1].session_id


def assert_provider_contract(provider: Provider, tmp_path: Path, *, proc: ProcInfo | None = None) -> None:
    assert provider.name and provider.label
    assert isinstance(provider.capabilities, Capabilities)
    for name in _SUPPORT_FIELDS:
        assert isinstance(getattr(provider.capabilities, name), Support), name
    assert isinstance(provider.capabilities.processes, bool)
    assert isinstance(provider.capabilities.history, bool)
    assert isinstance(provider.capabilities.resume, Support)
    assert isinstance(provider.label_key, str) and provider.label_key
    assert isinstance(provider.short_label_key, str) and provider.short_label_key
    assert provider.tier in TIERS
    assert isinstance(provider.verified_versions, tuple)
    assert all(isinstance(version, str) and version for version in provider.verified_versions)
    assert isinstance(provider.accent, str) and provider.accent
    assert provider.usage_basis() in USAGE_BASES
    assert isinstance(provider.home_env, str)
    assert issubclass(provider.session_cls, Session)
    assert all(isinstance(name, str) and name for name in provider.process_names)

    meta = provider.run_meta(None, None)
    assert isinstance(meta, dict)
    assert meta["label_key"] == provider.label_key
    assert meta["label"] == provider.label
    assert meta["accent"] == provider.accent
    assert meta["usage_basis"] == provider.usage_basis()
    assert "origin_kind" not in meta

    since = time.time() - 3600
    refs = list(provider.discover(since)) if provider.capabilities.history else []
    keys: set[str] = set()
    for ref in refs:
        _check_ref(provider, ref, keys)
    if provider.capabilities.history:
        assert set(refs) == set(provider.discover(since))  # discovery is idempotent

    sessions = [_check_reader(provider, ref) for ref in refs[:2]]
    for session in sessions:
        _check_capabilities(provider, session)
        _check_origin(provider, session)
        _check_quota(provider, session)
    _check_locator_identity(provider, refs)
    _check_match(provider, proc)

    file_ref = next((ref for ref in refs if ref.kind == "file"), None)
    if file_ref is None:
        return

    sample_reader = provider.open(file_ref)
    sample_reader.update()
    sample = sample_reader.session()
    spec = provider.resume(Run("kit", provider.name, sample, None, "kit"))
    if spec is not None:
        assert isinstance(spec.get("argv"), list) and spec["argv"]
        assert all(isinstance(arg, str) and arg for arg in spec["argv"])
        assert spec.get("cwd") is None or isinstance(spec.get("cwd"), str)
        assert render_resume(spec, "sh")

    fuzz = tmp_path / f"fuzz-{provider.name}-{file_ref.locator.name}"
    fuzz.write_bytes(file_ref.locator.read_bytes() + b'{"broken":\nnot json\n')
    parsed_reader = provider.open(provider.ref_for(fuzz))
    parsed_reader.update()
    parsed = parsed_reader.session()
    assert parsed.discarded >= 1  # corrupt lines stay inside the parser
    assert parsed_reader.health().state == "degraded"  # and are visible as degraded health
