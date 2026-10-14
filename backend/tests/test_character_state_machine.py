import pytest

from baymax.character.state_machine import (
    EVENT_TARGETS,
    TRANSITIONS,
    CharacterEvent,
    CharacterState,
    CharacterStateMachine,
)


def test_tables_match_shared_protocol(spec):
    assert [s.value for s in CharacterState] == spec["characterStates"]
    for src, dsts in spec["characterTransitions"].items():
        assert {d.value for d in TRANSITIONS[CharacterState(src)]} == set(dsts), src
    assert {e.value: t.value for e, t in EVENT_TARGETS.items()} == spec["characterEvents"]


def test_shared_test_vectors(spec):
    for vec in spec["characterTestVectors"]:
        sm = CharacterStateMachine(CharacterState(vec["from"]))
        res = sm.dispatch(vec["event"], verified=vec.get("verified", False))
        assert sm.state.value == vec["expect"], vec
        assert res.accepted == (not vec.get("rejected", False)), vec


def test_success_requires_verification_and_executing():
    sm = CharacterStateMachine()
    sm.dispatch(CharacterEvent.REQUEST_RECEIVED)
    assert not sm.dispatch(CharacterEvent.TASK_VERIFIED, verified=True).accepted  # THINKING -> SUCCESS not allowed
    sm.dispatch(CharacterEvent.TASK_STARTED)
    res = sm.dispatch(CharacterEvent.TASK_VERIFIED)  # not verified
    assert not res.accepted and "verified" in res.reason
    assert sm.dispatch(CharacterEvent.TASK_VERIFIED, verified=True).accepted
    assert sm.state is CharacterState.SUCCESS


@pytest.mark.parametrize("event", [e for e in CharacterEvent if e not in (CharacterEvent.WAKE, CharacterEvent.CONNECTION_LOST, CharacterEvent.SLEEP)])
def test_sleeping_ignores_everything_but_wake_and_disconnect(event):
    sm = CharacterStateMachine(CharacterState.SLEEPING)
    sm.dispatch(event, verified=True)
    assert sm.state is CharacterState.SLEEPING


def test_disconnected_only_restores():
    sm = CharacterStateMachine()
    sm.dispatch(CharacterEvent.CONNECTION_LOST)
    for e in (CharacterEvent.MIC_STARTED, CharacterEvent.REQUEST_RECEIVED, CharacterEvent.TASK_STARTED, CharacterEvent.SPEECH_STARTED):
        assert not sm.dispatch(e).accepted
    assert sm.dispatch(CharacterEvent.CONNECTION_RESTORED).accepted
    assert sm.state is CharacterState.IDLE


def test_listeners_only_fire_on_real_change():
    seen = []
    sm = CharacterStateMachine()
    sm.on_change(lambda r: seen.append(r.state))
    sm.dispatch(CharacterEvent.RESET)  # IDLE -> IDLE: no change
    sm.dispatch(CharacterEvent.MIC_STARTED)
    sm.dispatch(CharacterEvent.SPEECH_STARTED)  # rejected while listening
    assert seen == [CharacterState.LISTENING]


def test_every_state_reaches_idle_or_has_exit():
    for state, dsts in TRANSITIONS.items():
        assert dsts, f"{state} is a dead end"
        assert CharacterState.DISCONNECTED in dsts or state is CharacterState.DISCONNECTED


def test_rule_sets_match_protocol(spec):
    from baymax.character.state_machine import DISCONNECTED_EXIT_EVENTS, SLEEP_EXIT_EVENTS

    assert {e.value for e in SLEEP_EXIT_EVENTS} == set(spec["characterRules"]["sleepExitEvents"])
    assert {e.value for e in DISCONNECTED_EXIT_EVENTS} == set(spec["characterRules"]["disconnectedExitEvents"])
