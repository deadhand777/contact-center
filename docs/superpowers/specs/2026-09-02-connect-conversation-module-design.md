# Connect Conversation Module Design

## Goal

Deepen the Connect conversation module so one interface owns the lifecycle and
timing of an Amazon Connect chat conversation. Remove the duplicated transcript
polling algorithm from the live integration test while preserving existing CLI
and runtime behavior.

## Scope

This refactor changes only the internal Connect chat harness and its tests:

- `src/contact_center/_internal/connect_chat.py`
- `tests/test_connect_chat.py`
- `tests/test_connect_integration.py`

It does not change AWS infrastructure, the Connect contact flow, Lex, the bridge
Lambda, the AgentCore runtime contract, CLI arguments, user-visible text, or
dependencies.

## Current Friction

The current module exposes contact identifiers, participant credentials, the
websocket, transcript cursor state, polling, settle timing, and cleanup through
`ChatSession` and several module-level functions. Callers must compose these
details correctly.

The live integration test therefore duplicates the production polling and
settling implementation instead of testing through the module interface. It
also repeats websocket cleanup. This weakens locality and creates two places
where Connect timing behavior can drift.

The deletion test confirms that the module is shallow: deleting the lifecycle
helpers would move their implementation into callers, while retaining them
beside a new object would leave the interface nearly as complex as the
implementation.

## Chosen Design

Use a stateful `ConnectConversation` object in the existing `connect_chat`
module. It owns:

- the Connect participant client;
- the contact ID and connection token;
- the optional websocket;
- the set of transcript item IDs already seen;
- transcript polling and event interpretation;
- settle and timeout timing; and
- guarded websocket cleanup.

The module keeps one construction interface that starts the Connect contact,
creates the participant connection, opens and subscribes the websocket when
present, and returns a ready conversation.

The conversation exposes a small behavioral interface:

- send customer text;
- wait for a turn to settle; and
- close the conversation.

Context-manager support guarantees that production and integration-test callers
use the same cleanup path.

The existing `ChatSession`, `start_session`, `send`, `poll_events`, and `_drain`
interfaces are removed after all repository callers move to the conversation
interface. They are internal and receive no compatibility aliases.

## Turn Result

Waiting returns an immutable structured result rather than printing or exposing
raw transcript items. The result contains:

- customer-visible system or agent messages in transcript order;
- whether the contact transferred;
- whether the chat ended; and
- whether waiting timed out before another terminal condition.

A transfer or ended event stops waiting immediately. Otherwise, waiting stops
after at least one new system/agent message or terminal event has arrived and
no further relevant event appears during the settle interval. Customer echoes
are marked seen but do not start or extend settling. Transcript IDs remain owned
by the conversation, so later waits return only newly observed messages and
events.

The result is the test surface. The CLI adapter decides how to print messages
and the existing transfer/end notices.

## Runtime Flow

`run_connect_chat` remains responsible for resolving SSM configuration and
constructing boto3 clients. It then:

1. opens a `ConnectConversation` in a context manager;
2. waits for and prints the greeting;
3. sends a one-shot question or each interactive prompt;
4. waits for and prints the structured turn result; and
5. stops when the result reports transfer or end.

The live integration test uses the same construction, send, wait, and close
behavior. It may choose its own timeout, but it does not implement transcript
polling, settling, deduplication, or socket cleanup.

## Behavior Preservation

This is a structural refactor. Preserve these behaviors:

- the websocket subscription occurs before the first customer message;
- a missing websocket remains allowed;
- the default wait timeout remains 45 seconds;
- the default settle interval remains 3 seconds;
- polling remains once per second;
- customer transcript echoes remain excluded;
- system and agent messages remain visible;
- transfer and ended events stop the interactive loop;
- timeout remains non-fatal to the CLI;
- setup, send, and transcript transport exceptions continue to propagate;
- SSM configuration failures retain the existing `SystemExit` message; and
- websocket close failures remain suppressed.

No new retry or error-translation policy is introduced.

## Testing

Write the focused tests before changing the implementation. Tests cover:

- construction forwards `customer_id` and stores returned identifiers;
- a websocket is opened and subscribed when Connect provides one;
- missing websocket behavior is preserved;
- sending uses the owned participant client and connection token;
- transcript IDs are deduplicated across successive waits;
- a settled turn returns ordered messages;
- transfer and ended states stop waiting immediately;
- timeout is distinguishable from a settled turn;
- context-manager exit closes the websocket;
- websocket close failures are suppressed; and
- `run_connect_chat` remains a printing adapter over structured results.

Update the live Connect integration test to use the same conversation interface
for greeting and answer turns. Its existing assertions remain unchanged:
balance and knowledge answers arrive without transfer, and explicit escalation
reaches the configured queue.

Run the focused `test_connect_chat.py` suite before and after the implementation.
Then run `python scripts/make check` as the repository completion gate. Live AWS
integration remains explicitly gated and is not required for the local refactor
proof unless credentials and the deployed environment are intentionally used.

## Alternatives Rejected

### Functional conversation engine

Passing immutable state through module-level functions would ease isolated
testing but retain a wide interface. Callers would still coordinate the
participant client, token, transcript cursor, and cleanup, so the module would
remain shallow.

### Separate transport adapter and conversation module

Splitting boto3 and websocket access from timing orchestration would create a
new seam with only one production adapter. That is a hypothetical seam and adds
structure without current leverage.

### Compatibility aliases

Keeping the old helper functions as forwarding aliases would preserve the wide
interface and allow new callers to bypass conversation ownership. All callers
are internal to this repository, so they move in the same change.

## Success Criteria

- Production and live integration code use one transcript polling algorithm.
- Callers do not own transcript cursor state or websocket cleanup.
- The conversation interface returns structured turn results and performs no
	printing; `run_connect_chat` remains the colocated printing adapter.
- Existing CLI output and stop behavior are preserved.
- Focused tests and the repository check pass without new dependencies.
