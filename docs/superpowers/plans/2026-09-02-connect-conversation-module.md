# Connect Conversation Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shallow Connect chat helpers with one state-owning conversation interface used by both the CLI and live integration tests.

**Architecture:** A `ConnectConversation` object in the existing `connect_chat` module owns the participant client, connection token, websocket, transcript cursor, wait algorithm, and cleanup. Its `wait()` method returns an immutable `TurnResult`; the colocated `run_connect_chat()` adapter alone prints user-visible output.

**Tech Stack:** Python 3.13+, dataclasses, boto3 Amazon Connect and Connect Participant clients, websocket-client, pytest

**Spec:** `docs/superpowers/specs/2026-09-02-connect-conversation-module-design.md`

## Global Constraints

- Preserve CLI arguments, output text, timeout behavior, and exception behavior.
- Keep the default wait timeout at 45 seconds, settle interval at 3 seconds, and poll interval at 1 second.
- Allow Connect participant connections that omit websocket information.
- Suppress websocket close failures.
- Add no runtime dependency and create no new Python module.
- Use absolute imports under `src/`.
- Do not change infrastructure, the Connect flow, Lex, the bridge Lambda, or the AgentCore response contract.
- Remove `ChatSession`, `start_session`, `send`, `poll_events`, and `_drain` after all callers migrate.
- Leave `.agents/` and `skills-lock.json` untracked and untouched.

---

### Task 1: Give one object ownership of the conversation lifecycle

**Files:**
- Modify: `src/contact_center/_internal/connect_chat.py`
- Test: `tests/test_connect_chat.py`

**Interfaces:**
- Consumes: existing `_open_chat_socket(url: str) -> websocket.WebSocket`
- Produces: `ConnectConversation.start(customer_id: str, *, connect: Any, participant: Any, instance_id: str, flow_id: str) -> ConnectConversation`
- Produces: `ConnectConversation.send(text: str) -> None`
- Produces: `ConnectConversation.close() -> None`
- Produces: context-manager methods returning the same conversation and closing it on exit

- [ ] **Step 1: Replace the session-construction tests with failing conversation lifecycle tests**

Keep `_FakeConnect` and `_FakeParticipant`, then replace tests that call
`start_session` and add lifecycle coverage:

```python
class _FakeSocket:
    def __init__(self, error: Exception | None = None) -> None:
        self.closed = False
        self._error = error

    def close(self) -> None:
        self.closed = True
        if self._error is not None:
            raise self._error


def test_start_conversation_passes_customer_attribute() -> None:
    connect = _FakeConnect()
    participant = _FakeParticipant()

    conversation = connect_chat.ConnectConversation.start(
        "KND-1001",
        connect=connect,
        participant=participant,
        instance_id="inst",
        flow_id="flow",
    )

    assert conversation.contact_id == "contact-1"
    assert connect.calls[0]["Attributes"] == {"customer_id": "KND-1001"}
    assert conversation.socket is None


def test_start_conversation_opens_websocket_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeParticipantWithSocket(_FakeParticipant):
        def create_participant_connection(self, **_kwargs: Any) -> dict:
            return {
                "ConnectionCredentials": {"ConnectionToken": "ctoken"},
                "Websocket": {"Url": "wss://x"},
            }

    socket = _FakeSocket()
    monkeypatch.setattr(connect_chat, "_open_chat_socket", lambda _url: socket)

    conversation = connect_chat.ConnectConversation.start(
        "KND-1001",
        connect=_FakeConnect(),
        participant=_FakeParticipantWithSocket(),
        instance_id="inst",
        flow_id="flow",
    )

    assert conversation.socket is socket


def test_conversation_sends_with_owned_connection() -> None:
    participant = _FakeParticipant()
    conversation = connect_chat.ConnectConversation(
        contact_id="contact-1",
        connection_token="ctoken",
        participant=participant,
    )

    conversation.send("Hallo")

    assert participant.sent == ["Hallo"]


@pytest.mark.parametrize("error", [None, RuntimeError("close failed")])
def test_context_manager_closes_socket_and_suppresses_failures(error: Exception | None) -> None:
    socket = _FakeSocket(error)
    conversation = connect_chat.ConnectConversation(
        contact_id="contact-1",
        connection_token="ctoken",
        participant=_FakeParticipant(),
        socket=socket,
    )

    with conversation as entered:
        assert entered is conversation

    assert socket.closed is True
```

- [ ] **Step 2: Run the focused lifecycle tests and verify they fail**

Run:

```bash
python scripts/make test -- \
  tests/test_connect_chat.py::test_start_conversation_passes_customer_attribute \
  tests/test_connect_chat.py::test_start_conversation_opens_websocket_when_present \
  tests/test_connect_chat.py::test_conversation_sends_with_owned_connection \
  tests/test_connect_chat.py::test_context_manager_closes_socket_and_suppresses_failures
```

Expected: FAIL because `connect_chat.ConnectConversation` does not exist.

- [ ] **Step 3: Implement lifecycle ownership without changing existing callers**

Add this class after `_open_chat_socket`; keep the old `ChatSession` and helpers
temporarily so the repository remains runnable between tasks:

```python
class ConnectConversation:
    """One Amazon Connect chat conversation and its owned transport state."""

    def __init__(
        self,
        *,
        contact_id: str,
        connection_token: str,
        participant: Any,
        socket: Any = None,
    ) -> None:
        """Store the participant connection and optional websocket."""
        self.contact_id = contact_id
        self._connection_token = connection_token
        self._participant = participant
        self.socket = socket
        self._seen: set[str] = set()

    @classmethod
    def start(
        cls,
        customer_id: str,
        *,
        connect: Any,
        participant: Any,
        instance_id: str,
        flow_id: str,
    ) -> ConnectConversation:
        """Start a Connect contact and open its participant connection."""
        started = connect.start_chat_contact(
            InstanceId=instance_id,
            ContactFlowId=flow_id,
            Attributes={"customer_id": customer_id},
            ParticipantDetails={"DisplayName": "PoC Customer"},
            ClientToken=uuid.uuid4().hex,
        )
        connection = participant.create_participant_connection(
            ParticipantToken=started["ParticipantToken"],
            Type=["WEBSOCKET", "CONNECTION_CREDENTIALS"],
        )
        websocket_info = connection.get("Websocket")
        socket = _open_chat_socket(websocket_info["Url"]) if websocket_info else None
        return cls(
            contact_id=started["ContactId"],
            connection_token=connection["ConnectionCredentials"]["ConnectionToken"],
            participant=participant,
            socket=socket,
        )

    def __enter__(self) -> ConnectConversation:
        """Return this open conversation."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close this conversation when leaving its context."""
        self.close()

    def send(self, text: str) -> None:
        """Send one customer message into the chat."""
        self._participant.send_message(
            ConnectionToken=self._connection_token,
            ContentType="text/plain",
            Content=text,
        )

    def close(self) -> None:
        """Close the websocket, suppressing cleanup failures."""
        if self.socket is not None:
            with contextlib.suppress(Exception):
                self.socket.close()
```

- [ ] **Step 4: Run all Connect chat unit tests**

Run: `python scripts/make test -- tests/test_connect_chat.py`

Expected: PASS. Existing tests still pass through the temporary old helpers;
new tests pass through `ConnectConversation`.

- [ ] **Step 5: Commit the independently working lifecycle change**

```bash
git add src/contact_center/_internal/connect_chat.py tests/test_connect_chat.py
git commit -m "refactor: encapsulate Connect conversation lifecycle"
```

---

### Task 2: Move transcript progress and timing behind `wait()`

**Files:**
- Modify: `src/contact_center/_internal/connect_chat.py`
- Test: `tests/test_connect_chat.py`

**Interfaces:**
- Consumes: `ConnectConversation` from Task 1 with owned `_participant`, `_connection_token`, and `_seen`
- Produces: immutable `TurnResult(messages: tuple[str, ...], transferred: bool = False, ended: bool = False, timed_out: bool = False)`
- Produces: `ConnectConversation.wait(*, timeout: float = 45.0, settle: float = 3.0) -> TurnResult`

- [ ] **Step 1: Make the fake participant expose a mutable transcript**

Keep the fake's existing constructor shape and replace `get_transcript` with:

```python
def get_transcript(self, **_kwargs: Any) -> dict:
    """Return the current scripted transcript."""
    return {"Transcript": self.items}
```

- [ ] **Step 2: Write failing tests for structured results and owned cursor state**

```python
def _conversation(items: list[dict]) -> tuple[connect_chat.ConnectConversation, _FakeParticipant]:
    participant = _FakeParticipant(items)
    conversation = connect_chat.ConnectConversation(
        contact_id="contact-1",
        connection_token="ctoken",
        participant=participant,
    )
    return conversation, participant


def test_wait_returns_ordered_messages_after_settle() -> None:
    conversation, _ = _conversation([
        {"Id": "1", "Type": "MESSAGE", "ParticipantRole": "CUSTOMER", "Content": "hi"},
        {"Id": "2", "Type": "MESSAGE", "ParticipantRole": "SYSTEM", "Content": "Willkommen"},
        {"Id": "3", "Type": "MESSAGE", "ParticipantRole": "AGENT", "Content": "Wie kann ich helfen?"},
    ])

    result = conversation.wait(settle=0)

    assert result == connect_chat.TurnResult(messages=("Willkommen", "Wie kann ich helfen?"))


def test_wait_owns_transcript_cursor_across_turns() -> None:
    conversation, participant = _conversation([
        {"Id": "1", "Type": "MESSAGE", "ParticipantRole": "SYSTEM", "Content": "Willkommen"},
    ])
    assert conversation.wait(settle=0).messages == ("Willkommen",)
    participant.items.append(
        {"Id": "2", "Type": "MESSAGE", "ParticipantRole": "AGENT", "Content": "Antwort"},
    )

    assert conversation.wait(settle=0).messages == ("Antwort",)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        (
            "application/vnd.amazonaws.connect.event.transfer.succeeded",
            connect_chat.TurnResult(messages=(), transferred=True),
        ),
        (
            "application/vnd.amazonaws.connect.event.chat.ended",
            connect_chat.TurnResult(messages=(), ended=True),
        ),
    ],
)
def test_wait_returns_terminal_state_immediately(content_type: str, expected: object) -> None:
    conversation, _ = _conversation([
        {"Id": "1", "Type": "EVENT", "ContentType": content_type},
    ])

    assert conversation.wait() == expected


def test_wait_reports_timeout() -> None:
    conversation, _ = _conversation([])

    assert conversation.wait(timeout=0) == connect_chat.TurnResult(messages=(), timed_out=True)
```

- [ ] **Step 3: Run the wait tests and verify they fail**

Run:

```bash
python scripts/make test -- \
  tests/test_connect_chat.py::test_wait_returns_ordered_messages_after_settle \
  tests/test_connect_chat.py::test_wait_owns_transcript_cursor_across_turns \
  tests/test_connect_chat.py::test_wait_returns_terminal_state_immediately \
  tests/test_connect_chat.py::test_wait_reports_timeout
```

Expected: FAIL because `TurnResult` and `ConnectConversation.wait` do not exist.

- [ ] **Step 4: Add the immutable result and move polling into the conversation**

Add `TurnResult` before `ConnectConversation`:

```python
@dataclass(frozen=True)
class TurnResult:
    """New customer-visible output and terminal state from one wait."""

    messages: tuple[str, ...]
    transferred: bool = False
    ended: bool = False
    timed_out: bool = False
```

Add this method to `ConnectConversation`:

```python
def wait(self, *, timeout: float = 45.0, settle: float = 3.0) -> TurnResult:
    """Wait for new transcript output to settle or reach a terminal state."""
    deadline = time.monotonic() + timeout
    messages: list[str] = []
    last_new: float | None = None
    while time.monotonic() < deadline:
        transcript = self._participant.get_transcript(ConnectionToken=self._connection_token)
        new_items: list[dict[str, Any]] = []
        for item in transcript.get("Transcript", []):
            item_id = item.get("Id", "")
            if item_id in self._seen:
                continue
            self._seen.add(item_id)
            new_items.append(item)
        transferred = False
        ended = False
        relevant_event = False
        for item in new_items:
            if item.get("Type") == "MESSAGE" and item.get("ParticipantRole") in {"SYSTEM", "AGENT"}:
                messages.append(item.get("Content", ""))
                relevant_event = True
            elif item.get("Type") == "EVENT" and item.get("ContentType") == _TRANSFER_EVENT:
                transferred = True
                relevant_event = True
            elif item.get("Type") == "EVENT" and item.get("ContentType") == _ENDED_EVENT:
                ended = True
                relevant_event = True
        if relevant_event:
            last_new = time.monotonic()
        if transferred or ended:
            return TurnResult(tuple(messages), transferred=transferred, ended=ended)
        if last_new is not None and time.monotonic() - last_new >= settle:
            return TurnResult(tuple(messages))
        time.sleep(1)
    return TurnResult(tuple(messages), timed_out=True)
```

Before running tests, compare this implementation to the old `poll_events` and
`_drain`: every transcript item is marked seen, customer echoes are omitted from
messages and do not affect settling, relevant events determine state and reset
the settle timer, and transport exceptions are not caught.

- [ ] **Step 5: Run all Connect chat unit tests**

Run: `python scripts/make test -- tests/test_connect_chat.py`

Expected: PASS.

- [ ] **Step 6: Commit the independently working wait behavior**

```bash
git add src/contact_center/_internal/connect_chat.py tests/test_connect_chat.py
git commit -m "refactor: move Connect polling behind conversation wait"
```

---

### Task 3: Migrate callers and remove the shallow interface

**Files:**
- Modify: `src/contact_center/_internal/connect_chat.py`
- Modify: `tests/test_connect_chat.py`
- Modify: `tests/test_connect_integration.py`

**Interfaces:**
- Consumes: `ConnectConversation.start`, `ConnectConversation.send`, `ConnectConversation.wait`, and context-manager cleanup from Tasks 1-2
- Consumes: `TurnResult.messages`, `TurnResult.transferred`, `TurnResult.ended`, and `TurnResult.timed_out`
- Produces: no new reusable interface; removes the old session and helper interfaces

- [ ] **Step 1: Add a failing adapter test for structured turn output**

Add a private printing-adapter test:

```python
def test_print_turn_preserves_messages_and_terminal_notices(capsys: pytest.CaptureFixture[str]) -> None:
    keep_going = connect_chat._print_turn(
        connect_chat.TurnResult(messages=("Antwort",), transferred=True),
    )

    assert keep_going is False
    assert capsys.readouterr().out == "Antwort\n⚠ Chat wurde an die Warteschlange übergeben.\n"
```

- [ ] **Step 2: Run the adapter test and verify it fails**

Run: `python scripts/make test -- tests/test_connect_chat.py::test_print_turn_preserves_messages_and_terminal_notices`

Expected: FAIL because `_print_turn` does not exist.

- [ ] **Step 3: Implement the thin printing adapter**

```python
def _print_turn(result: TurnResult) -> bool:
    """Print one turn and return whether the conversation can continue."""
    for message in result.messages:
        print(message)  # noqa: T201
    if result.transferred:
        print("⚠ Chat wurde an die Warteschlange übergeben.")  # noqa: T201
    if result.ended:
        print("(Chat beendet.)")  # noqa: T201
    return not (result.transferred or result.ended)
```

- [ ] **Step 4: Rewrite `run_connect_chat` around the deep interface**

Keep existing SSM and boto3 setup, then replace session orchestration with:

```python
with ConnectConversation.start(
    customer_id,
    connect=connect,
    participant=participant,
    instance_id=instance_id,
    flow_id=flow_id,
) as conversation:
    _print_turn(conversation.wait())
    if question is not None:
        conversation.send(question)
        _print_turn(conversation.wait())
        return 0
    while True:  # pragma: no cover (interactive loop)
        try:
            prompt = input("you> ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if prompt.strip().lower() in {"exit", "quit"}:
            return 0
        conversation.send(prompt)
        if not _print_turn(conversation.wait()):
            return 0
```

Do not make greeting terminal state stop the CLI: the old implementation printed
the greeting drain result but did not branch on its boolean return. This preserves
behavior exactly.

- [ ] **Step 5: Rewrite the live integration helper to use the same wait algorithm**

Remove `contextlib` and `time` imports from `tests/test_connect_integration.py`.
Replace `_chat_turn` with:

```python
def _chat_turn(
    clients: dict,
    question: str,
    customer_id: str = "KND-1001",
    timeout: float = 90.0,
) -> tuple[list[str], bool, str]:
    """Run one question through Connect; return messages, transfer state, and contact id."""
    with connect_chat.ConnectConversation.start(
        customer_id,
        connect=clients["connect"],
        participant=clients["participant"],
        instance_id=clients["instance_id"],
        flow_id=clients["flow_id"],
    ) as conversation:
        conversation.wait()
        conversation.send(question)
        result = conversation.wait(timeout=timeout)
        return list(result.messages), result.transferred, conversation.contact_id
```

Keep the three existing integration assertions unchanged.

- [ ] **Step 6: Remove old tests and old interfaces**

Delete tests that directly exercise `ChatSession`, `start_session`, and
`poll_events`. Delete these definitions from `connect_chat.py`:

```python
@dataclass
class ChatSession: ...

def start_session(...): ...
def send(...): ...
def poll_events(...): ...
def _drain(...): ...
```

Retain `_open_chat_socket`, because `ConnectConversation.start` owns and uses it.

- [ ] **Step 7: Prove the old interface has no references**

Run:

```bash
grep -R -n -E 'ChatSession|start_session|poll_events|_drain' \
  src/contact_center tests --include='*.py'
```

Expected: no output and exit status 1. A match means migration is incomplete.

- [ ] **Step 8: Run focused unit tests**

Run: `python scripts/make test -- tests/test_connect_chat.py tests/test_cli.py`

Expected: PASS.

- [ ] **Step 9: Run the broader local integration-test module without enabling AWS calls**

Run: `uv run pytest -c config/pytest.ini tests/test_connect_integration.py -m integration`

Expected: all tests SKIPPED because `RUN_INTEGRATION` is unset; collection and imports succeed.

- [ ] **Step 10: Run the repository completion gate**

Run: `python scripts/make check`

Expected: quality, type, documentation, and configured test checks PASS.

- [ ] **Step 11: Review the final diff for behavior-only scope**

Run:

```bash
git diff --check
git diff --stat HEAD~2
git status --short
```

Expected: only `connect_chat.py`, its two test files, and this already-committed
design/plan history relate to the work. `.agents/` and `skills-lock.json` remain
untracked and are not staged.

- [ ] **Step 12: Commit the caller migration**

```bash
git add \
  src/contact_center/_internal/connect_chat.py \
  tests/test_connect_chat.py \
  tests/test_connect_integration.py
git commit -m "refactor: deepen Connect conversation module"
```

Do not run the live AWS integration suite by default. If the user explicitly
requests deployed verification and valid sandbox credentials are available, run:

```bash
AWS_PROFILE=chrisschulz.SandboxPermissionSet RUN_INTEGRATION=1 \
  uv run pytest -c config/pytest.ini -o addopts= \
  -m integration --no-cov tests/test_connect_integration.py
```
