import json

import pytest

from baymax.protocol import messages as proto
from baymax.tasks.models import TASK_TRANSITIONS, TaskStatus


def frame(type_, payload=None, **env):
    d = {"v": 1, "type": type_, "id": "abc123", "ts": 1, "payload": payload or {}}
    d.update(env)
    return json.dumps(d)


def test_schemas_match_shared_protocol(spec):
    for direction, table in (("clientToServer", proto.CLIENT_TO_SERVER), ("serverToClient", proto.SERVER_TO_CLIENT)):
        assert set(spec["messages"][direction]) == set(table)
        for name, s in spec["messages"][direction].items():
            req, opt = table[name]
            assert req == s["required"], name
            assert opt == s.get("optional", {}), name
            assert proto.SINCE.get(name, 1) == s.get("since", 1), name
    assert spec["version"] == proto.PROTOCOL_VERSION
    assert spec["minSupportedVersion"] == proto.MIN_PROTOCOL_VERSION
    assert spec["maxMessageBytes"] == proto.MAX_MESSAGE_BYTES
    assert set(spec["voiceStates"]) == proto.VOICE_STATES


def test_task_transitions_match_protocol(spec):
    assert [s.value for s in TaskStatus] == spec["taskStates"]
    for src, dsts in spec["taskTransitions"].items():
        assert {d.value for d in TASK_TRANSITIONS[TaskStatus(src)]} == set(dsts)


def test_valid_message_parses_and_drops_unknown_fields():
    m = proto.parse(frame("user.text", {"text": "hi", "evil": "x"}))
    assert m.type == "user.text" and m.payload == {"text": "hi"}


@pytest.mark.parametrize("raw,code", [
    ("{not json", "bad_json"),
    ("[1,2]", "bad_envelope"),
    (json.dumps({"type": "ping", "id": "a", "payload": {}}), "bad_envelope"),
    (frame("ping", v=99), "version_unsupported"),
    (frame("ping", v=3), "version_unsupported"),
    (frame("ping", v="1"), "bad_envelope"),
    (frame("shell.exec", {"cmd": "rm -rf /"}), "unknown_type"),
    (frame("hello.ack", {"protocol": 1, "server": "x"}), "unknown_type"),  # server->client type sent by client
    (frame("user.text", {}), "bad_payload"),
    (frame("user.text", {"text": 5}), "bad_payload"),
    (frame("user.text", {"text": "   "}), "bad_payload"),
    (frame("sleep.set", {"sleeping": "yes"}), "bad_payload"),
    (frame("hello", {"protocol": True, "minProtocol": 1, "client": "x"}), "bad_payload"),
    (frame("voice.state", {"state": "recording-secretly"}), "bad_payload"),
    (frame("voice.state", {"state": "listening", "level": float("nan")}).replace("NaN", "NaN"), "bad_payload"),
    (json.dumps({"v": 1, "type": "ping", "id": "x" * 100, "ts": 0, "payload": {}}), "bad_envelope"),
    (frame("ping", payload=None, ts="now"), "bad_envelope"),
])
def test_malformed_messages_rejected(raw, code):
    with pytest.raises(proto.ProtocolError) as exc:
        proto.parse(raw)
    assert exc.value.code == code


def test_oversized_message_rejected():
    big = frame("user.text", {"text": "a" * (proto.MAX_MESSAGE_BYTES + 10)})
    with pytest.raises(proto.ProtocolError) as exc:
        proto.parse(big)
    assert exc.value.code == "too_large"


def test_string_field_length_limit():
    with pytest.raises(proto.ProtocolError) as exc:
        proto.parse(frame("user.text", {"text": "a" * (proto.MAX_STRING + 1)}))
    assert exc.value.code == "bad_payload"


def test_make_validates_outbound():
    m = proto.make("character.state", {"state": "IDLE"})
    assert json.loads(m.to_json())["payload"] == {"state": "IDLE"}
    with pytest.raises(ValueError):
        proto.make("character.state", {})
    with pytest.raises(ValueError):
        proto.make("not.a.type", {})


def test_version_error_is_fatal():
    with pytest.raises(proto.ProtocolError) as exc:
        proto.parse(frame("ping", v=3))
    assert exc.value.fatal


def test_v1_and_v2_frames_accepted():
    assert proto.parse(frame("ping", v=1)).v == 1
    assert proto.parse(frame("ping", v=2)).v == 2


def test_v2_outbound_validation():
    proto.make("cursor.update", {"x": 0.5, "y": 0.25, "displayId": 1, "displayWidth": 1512, "displayHeight": 982, "vx": 0.5, "vy": 0.25})
    with pytest.raises(ValueError):
        proto.make("gaze.target", {"kind": "laser", "x": 0.1, "y": 0.1})
    with pytest.raises(ValueError):
        proto.make("cursor.update", {"x": "left", "y": 0, "displayId": 1, "displayWidth": 1, "displayHeight": 1})


def test_task_update_carries_kind_for_the_phone_face():
    from baymax.protocol import messages as proto
    m = proto.make("task.update", {"taskId": "t1", "status": "RUNNING", "title": "Research", "kind": "research"})
    assert m.payload["kind"] == "research"  # the phone shows the RESEARCHING expression from this
