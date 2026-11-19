# Cell events

## TLDR

Unlabeled statements describe the current code, read at the checkouts in [How this was checked](#how-this-was-checked-tldr). **Observed** marks output from a run. **Deduction** marks my inference. **Planned** marks work that a plan or a `trail-todo` comment describes and the code does not do. A reason is credited to the plan, schema or comment that states it. Links to Trail code point at the working tree. Links to JupyterGIS code are pinned to a commit.

### What happens when a cell runs ([Deep Dive](#what-happens-when-a-cell-runs-tldr))

You run a cell in a notebook. Trail records the run as two [`ExecutionEvent`](../../src/trail/jupyter/event.py#L526) records: `started` when the kernel takes the code, and `completed` or `errored` when it finishes. The records hold the code, the execution count, the duration, the outputs, the error, the notebook and the cell. **Observed:** a recorded kernel session replayed through Trail's kernel code gave a `started` and an `errored` record for a `1 / 0` cell, with `error: ZeroDivisionError: division by zero` and `duration: 0.089 s` ([what each record looks like](#what-each-record-looks-like)).

These terms are needed:

- **JupyterLab** is the browser workspace that holds notebooks and maps.
- A **notebook** is an `.ipynb` file made of **cells**. A cell is one block of code that you run. Trail tracks the notebook as an asset.
- A **kernel** is the process that runs a notebook's code. One kernel can serve several notebooks.
- **Jupyter Server** is the process behind JupyterLab. It connects the browser to the kernels and relays the browser's event messages to anything that subscribes.

Three steps stay distinct:

1. **You act.** You run a cell.
2. **Two reports are made.** JupyterLab's page tells Jupyter Server which cell sent the request ([what the browser reports](#what-the-browser-reports-tldr)). The kernel broadcasts the code, outputs, errors and status of the run to every client connected to it, and Trail is one of those clients ([what the kernel reports](#what-the-kernel-reports-tldr)).
3. **Trail records.** It joins the two reports by the request's id and appends one event for the start and one for the end ([joining the two reports](#joining-the-two-reports-tldr)).

There are two reports because neither has the whole story. The kernel's broadcast has the code but not the cell, and it cannot say which notebook sent the code when a kernel is shared. Only the page that sends a request knows its cell ([the emitter's docs][ef-events-md-requests]). Trail installs nothing in the kernel. The [IPython history report](../report/11-3-1-ipython-history.md) names the kernel's broadcast as the priority source and history as an optional follow-up, and the [event coverage report](../../scratch/11-12-2-event-coverage.md) lists "Nothing is installed in the kernel" among the results.

| Piece | Where | Role |
|---|---|---|
| emitter, [`wireExecutionRequests`][ef-wire-requests] | JupyterLab, in the browser | Sends one message per execute request ([what the browser reports](#what-the-browser-reports-tldr)). |
| [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) | `trail.jupyter.consumer`, reached as [`trail.jupyter.session.consumer`](../../src/trail/jupyter/session.py#L37) | Hands that message to the kernel code. |
| [`Kernels`](../../src/trail/jupyter/kernels.py#L139) | `trail.jupyter.kernels`, reached as [`trail.jupyter.session.kernels`](../../src/trail/jupyter/session.py#L43) | Keeps one connection per kernel, joins the two reports and logs the events ([what the kernel reports](#what-the-kernel-reports-tldr), [joining the two reports](#joining-the-two-reports-tldr)). |
| [`ExecutionEvent`](../../src/trail/jupyter/event.py#L526) | `trail.jupyter.event` | The record ([the record class](#the-record-class-tldr)). |

Skeleton of the Trail side, condensed from the source and not runnable as written:

```python
# trail/jupyter/consumer.py
class Consumer(Node):
    def receive(self, server, text, *, at=None) -> Message | None:
        if schema_id == NOTEBOOK:
            self._parent.kernels.request(server, record)

# trail/jupyter/kernels.py
class Kernels(Node):
    def request(self, server, record) -> None:
        self.requests[request_id] = Request(kernel_id=..., document_path=..., cell_id=...)

    def receive(self, kernel, text) -> ExecutionEvent | None:
        ...                                    # busy, execute_input, outputs, error, idle
        self.start(kernel, execution)          # logs `started`
        self.log(kernel, execution, verb, at)  # logs `completed` or `errored`

    def log(self, kernel, execution, verb, at) -> ExecutionEvent:
        event = ExecutionEvent(schema_id=..., data=data, server=..., src_path=..., timestamp=at)
        event.apply(self._trail)
```

### What the browser reports ([Deep Dive](#what-the-browser-reports-tldr))

Each time a notebook or console in the main area sends an `execute_request` to its kernel, the page posts one `notebook/v1` message with the verb `requested`. It carries the request's id, the kernel's id, the notebook's path and the cell's id. The message exists only to name the cell. The kernel's own messages name the request as their parent, so the id joins the two reports. **Deduction:** a kernel that no JupyterLab page drives, such as one used through the server's API, sends no such message, so Trail records its runs without a cell ([limits today](#limits-today-tldr)).

```ts
// python/jupytergis_core/src/eventFeed/executions.ts
kernel.anyMessage.connect((_, { msg, direction }) => {
  if (direction !== 'send' || msg.header.msg_type !== 'execute_request') return;
  emit({
    schema_id: NOTEBOOK_SCHEMA,
    data: { verb: 'requested', object: { document_path, kernel_id: kernel.id, request_id: msg.header.msg_id, cell_id } },
  });
});
```

### What the kernel reports ([Deep Dive](#what-the-kernel-reports-tldr))

Trail polls each subscribed server for its sessions, which names every kernel and the paths that use it. For each new kernel it opens a websocket to the kernel's channels and reads the kernel's IOPub broadcast, the channel every connected client receives. One execution is a run of messages that share a parent, the request: `status` `busy`, `execute_input` with the code and its count, any outputs, an `error`, and `status` `idle`. [`Kernels.receive`](../../src/trail/jupyter/kernels.py#L385) follows each request in an [`Execution`](../../src/trail/jupyter/kernels.py#L89) until `idle`. An execution whose `busy` message Trail missed is ignored.

```python
# trail/jupyter/session.py
class JupyterSession(Session):
    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            self.kernels.refresh()           # polls GET /api/sessions

# trail/jupyter/kernels.py
class Kernels(Node):
    async def listen(self, kernel) -> None:
        socket.write_message(history_request(kernel.history))
        while True:
            self.receive(kernel, await socket.read_message())
```

### Joining the two reports ([Deep Dive](#joining-the-two-reports-tldr))

The two reports arrive in either order. [`Kernels.request`](../../src/trail/jupyter/kernels.py#L159) remembers the page's message by request id. When the kernel's `execute_input` arrives, Trail logs `started` at once if it already knows the request. Otherwise it waits half a second for the page's message and logs `started` then, with or without a cell. A run that ends sooner is started just before its end is logged. The notebook comes from the page's message. Without one, a kernel that serves exactly one notebook names it. A kernel shared by several sessions leaves the record without a notebook.

```python
# trail/jupyter/kernels.py
def receive(self, kernel, text) -> ExecutionEvent | None:
    if request_id in self.requests:
        return self.start(kernel, execution)
    execution.timer = loop.call_later(self.wait, self.start, kernel, execution)

def log(self, kernel, execution, verb, at) -> ExecutionEvent:
    request = self.requests.get(execution.request_id)
    src_path, asset = self.resolve(kernel, request.document_path)   # or self.resolve(kernel)
    event.entry = asset
```

### Recovering cells that ran earlier ([Deep Dive](#recovering-cells-that-ran-earlier-tldr))

When Trail connects to a kernel, some cells may already have run. On every connection Trail asks the kernel for the inputs of its current run, and logs each one it has not seen as a `recovered` record. A recovered record has the code and the count. History keeps no time, outcome, notebook or cell, so the record has none of them, and its timestamp is the time of recovery. **Observed:** a history reply with three inputs, one of them already seen live, gave two `recovered` records.

```python
# trail/jupyter/kernels.py
def recover(self, kernel, reply) -> list[ExecutionEvent]:
    ...
    for entry in entries:
        if count in logged or count in running:
            continue
        data = {'verb': 'recovered', 'execution_count': count, 'code': str(entry[2]), ...}
        event = ExecutionEvent(schema_id=ExecutionEvent.schema, data=data, ...)
        event.apply(self._trail)
```

### The record class ([Deep Dive](#the-record-class-tldr))

[`ExecutionEvent`](../../src/trail/jupyter/event.py#L526) keeps everything in its `data` dictionary and stores no field of its own. Its schema id, `trail/notebook-execution/v1`, is Trail's own name, not a JupyterGIS schema. The properties read `data`: the count, the kernel and request ids, the code, the cell id, a `status` of `ok` or `error`, and the [`duration`](../../src/trail/jupyter/event.py#L574) between the kernel's `busy` and `idle`. A `started` record prints its code. A closing record prints the duration, the outputs and the error. A query can filter on all of them.

```python
# trail/jupyter/event.py
class ExecutionEvent(JupyterEvent):
    schema: ClassVar[str] = 'trail/notebook-execution/v1'
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'execution_count', 'kernel_id', 'request_id', 'status', 'code', 'cell_id',
    )

    @property
    def duration(self) -> float | None: ...    # ended_at - started_at, in seconds
```

### Storage and replay ([Deep Dive](#storage-and-replay-tldr))

Each record is one JSON line in the log, written when the event is applied. The line holds `data`, `schema_id`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58), the record's `id` and `timestamp`, and the notebook's asset id. When a project opens, the log is replayed and each record is rebuilt as an `ExecutionEvent`. The kernel code reads the replayed log to avoid recording a recovered input or an attached notebook twice. **Observed:** after reopening the project, all 12 execution and kernel records of a test run rebuilt to equal records.

```python
# trail/event.py
class Event:
    def to_record(self) -> dict: ...                  # one JSON line of the log
    @classmethod
    def from_record(cls, /, trail, resolved_entry=None, **record) -> Event: ...

# trail/jupyter/kernels.py
def recover(self, kernel, reply) -> list[ExecutionEvent]:
    logged = {event.execution_count for event in self._trail.events if ...}   # read from the replayed log
```

### Limits today ([Deep Dive](#limits-today-tldr))

**Current behavior:**

- **A cell comes only from the page's message.** A kernel that no page drives has runs without a cell ([what the browser reports](#what-the-browser-reports-tldr)).
- **A shared kernel without the page's message has no notebook.** The record lists the kernel's session paths instead ([joining the two reports](#joining-the-two-reports-tldr)).
- **A restart drops the runs in progress.** They keep their `started` record and never get an end ([Kernel events](11-15-3-kernel-event.md#restarts-tldr)).
- **Recovered records have no time, notebook or cell** ([recovering cells that ran earlier](#recovering-cells-that-ran-earlier-tldr)).
- **The code differs from the event coverage report in a few details.** Every record stores the code but only the start prints it, and only the kernel's current run is recovered ([limits today](#limits-today-tldr)).

### How this was checked ([Deep Dive](#how-this-was-checked-tldr))

The kernel, consumer, event and subscription tests pass: 76 passed and 1 skipped. All of `tests/jupyter` passes where `jupyter_server` is installed: 89 passed and 1 skipped. **Observed:** a scenario run replayed a recorded kernel session with the page's messages, and a live run used a real Jupyter Server and kernel. No JupyterLab page was run. The page's message comes from the emitter's source, its unit tests and its docs.

## Deep Dive

### What happens when a cell runs ([TLDR](#what-happens-when-a-cell-runs-deep-dive))

This section lists every stage between running a cell and a line in the log. The two reports travel on separate routes and meet in stage 9.

| # | Where | What happens | What can stop it |
|---|---|---|---|
| 1 | JupyterLab, [`wireExecutionRequests`][ef-wire-requests] | The page sees its own `execute_request` and builds a `notebook/v1` message. | A notebook that is not in the main area is not wired. |
| 2 | Jupyter Server, `POST /api/events` | Validates the message against the schema registered under its `schema_id` and puts it on the event bus. | A rejected message gives a `400` that only the browser console shows. |
| 3 | Trail, [`subscribe`](../../src/trail/jupyter/subscription.py#L14) | Reads the server's WebSocket feed at [`Server.endpoint`](../../src/trail/jupyter/server.py#L288) and passes each text message to the consumer. | A refused, unsupported or dropped connection. |
| 4 | [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) | Parses the message. A `notebook/v1` message goes to the kernel code. | Invalid JSON. |
| 5 | [`Kernels.request`](../../src/trail/jupyter/kernels.py#L159) | Remembers the message by request id. | A message without a request id or kernel id is skipped as `invalid payload`. |
| 6 | [`Kernels.refresh`](../../src/trail/jupyter/kernels.py#L208), [`poll`](../../src/trail/jupyter/kernels.py#L223) and [`update`](../../src/trail/jupyter/kernels.py#L239) | Finds the kernels of each subscribed server and connects to each new one. | A server that is not `subscribed`. |
| 7 | [`Kernels.listen`](../../src/trail/jupyter/kernels.py#L343) | Reads the kernel's websocket and passes each text frame on. | A connection error, retried after `backoff`. |
| 8 | [`Kernels.receive`](../../src/trail/jupyter/kernels.py#L385) | Follows each request's messages from `busy` to `idle`. | An execution whose `busy` was missed. |
| 9 | [`Kernels.start`](../../src/trail/jupyter/kernels.py#L554) and [`log`](../../src/trail/jupyter/kernels.py#L601) | Builds the event, finds the notebook and applies it. | |
| 10 | [`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) | Appends the record to `trail.events`, which writes one line to the log. | |

Stages 1 to 5 and stages 6 to 8 run independently. [`JupyterSession`](../../src/trail/jupyter/session.py#L21) creates both pieces as [`session.consumer`](../../src/trail/jupyter/session.py#L37) and [`session.kernels`](../../src/trail/jupyter/session.py#L43). Its [`run`](../../src/trail/jupyter/session.py#L88) loop wakes every [`interval`](../../src/trail/jupyter/session.py#L26) seconds, 2.0 by default, and calls [`Kernels.refresh`](../../src/trail/jupyter/kernels.py#L208). The page's message is also kept as a [`Message`](../../src/trail/jupyter/message.py#L25) in [`session.messages`](../../src/trail/jupyter/session.py#L49), with the outcome `used`. It never becomes an event of its own. The execution events are made by `Kernels`, and no `Message` points at them.

The record classes under [`Event`](../../src/trail/event.py#L59) and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), the log and its replay are shared with the other event families. [Layer events](11-15-1-layer-event.md#the-record-classes-tldr) explains them. This note covers what is specific to cells.

### What the browser reports ([TLDR](#what-the-browser-reports-deep-dive))

This section covers what the emitter sends and when. The emitter lives in JupyterGIS, not in Trail. Its code is in the clone `../eventfeed`, a prototype that adds the event feed to JupyterGIS. The cell message is one of the prototype's additions ([event coverage report](../../scratch/11-12-2-event-coverage.md)), and the JupyterGIS maintainers have not reviewed it yet.

#### Where the messages come from

[`eventFeedPlugin`][ef-plugin] calls [`wireShellExecutions`][ef-wire-shell] once, when the plugin starts, at [`plugin.ts`][ef-plugin-wire]. The function scans the widgets in the main area for one that has a `sessionContext`, which is how a notebook or a console shows its kernel. It scans at start and again whenever the layout or the current widget changes, so notebooks opened later are found. A `WeakSet` keeps each context wired once. A shell that has no `widgets()` function, such as an embedded one, is skipped.

For each context, [`wireExecutionRequests`][ef-wire-requests] listens to the kernel's `anyMessage` signal, which reports every message the page sends or receives. [`attach`][ef-attach] connects it again when the context gets another kernel, and [`dispose`][ef-dispose] disconnects it when the context is disposed.

```ts
if (
  direction !== 'send' ||
  msg.header.msg_type !== 'execute_request' ||
  !kernel
) {
  return;
}
const metadata = (msg as KernelMessage.IExecuteRequestMsg).metadata as {
  cellId?: unknown;
};
const cellId = metadata?.cellId;
```

The page sends the cell's id in the request's `metadata.cellId`. The emitter forwards it when it is a string. [`localPath`][ef-local-path] removes a drive prefix such as `RTC:` from the notebook's path, because a collaborative document's path carries its drive. The source comment states the purpose: the request id joins a cell to the kernel's broadcast of its execution.

#### The message

The schema is [`notebook_v1.yaml`][ef-schema-notebook]. Trail keeps a copy for its tests as [`tests/jupyter/schemas/notebook_v1.yaml`](../../tests/jupyter/schemas/notebook_v1.yaml). **Observed:** a manual `diff` found no difference between the two. The round-trip test [`test_schema_registry_and_roundtrip`](../../tests/jupyter/test_events.py#L37) has no case for this schema, so only [`test_schema_copies_match_installed_eventfeed`](../../tests/jupyter/test_events.py#L289) compares the copy, and it skips where `jupytergis_core` is not installed. The schema requires `verb` and `object`, and the object requires `document_path`, `kernel_id` and `request_id`.

| Field | Content |
|---|---|
| `verb` | Always `requested`. |
| `time` | The page's clock when it sent the request. Trail keeps it only on the `Message` in `session.messages`. No cell record stores it. |
| `object.document_path` | The notebook's or console's path, or `null`. |
| `object.kernel_id` | The id of the kernel the request went to. |
| `object.request_id` | The `msg_id` of the execute request. |
| `object.cell_id` | The cell's id, when the page supplied one. |

This message is hand-written to match the schema:

```json
{
  "schema_id": "https://schema.jupytergis.org/events/notebook/v1",
  "verb": "requested",
  "time": "2026-11-12T10:33:18.800Z",
  "object": {
    "document_path": "analysis.ipynb",
    "kernel_id": "K1",
    "request_id": "cbadfa30-6041-4f5a-8c5a-1f0c2d6a1d11",
    "cell_id": "cell-1"
  }
}
```

The emitter's unit tests cover the message and the wiring: [`reports the cell and request id`][ef-test-requests], [`follows a kernel change`][ef-test-kernel-change] and [`wires each notebook in the main area once`][ef-test-wired-once]. **Observed:** the 49 tests of the emitter's event feed pass ([How this was checked](#how-this-was-checked-tldr)).

#### Delivery to Trail

The page sends the message with `events.emit`, which posts it to Jupyter Server. The server validates it against the schema that [`register_event_schemas`][ef-register] registered. It rejects an unregistered id or a mismatched version with a `400`. The rejection reaches only the browser console ([Silent failures][ef-events-md-silent]). A cell run can therefore fail to get its cell id without any error in JupyterLab.

### What the kernel reports ([TLDR](#what-the-kernel-reports-deep-dive))

This section covers how Trail finds the kernels and reads their broadcast. How a kernel is tied to a notebook, and how a restart is noticed, is covered in [Kernel events](11-15-3-kernel-event.md#finding-kernels-and-their-notebooks-tldr). This note uses the result.

#### Connecting to a kernel

[`Kernels`](../../src/trail/jupyter/kernels.py#L139) is a [`Node`](../../src/trail/node.py#L13) of the session. Its [`refresh`](../../src/trail/jupyter/kernels.py#L208) starts one [`poll`](../../src/trail/jupyter/kernels.py#L223) per server whose state is `subscribed` and whose last poll is done. A poll reads `GET {url}api/sessions` with the server's token. [`update`](../../src/trail/jupyter/kernels.py#L239) builds a [`Kernel`](../../src/trail/jupyter/kernels.py#L113) for each kernel id it has not seen and calls [`connect`](../../src/trail/jupyter/kernels.py#L338).

`connect` starts [`listen`](../../src/trail/jupyter/kernels.py#L343) as a task. `listen` opens `ws://host/api/kernels/<id>/channels` with a fresh `session_id` and the server's token, and sends a history request ([recovering cells that ran earlier](#recovering-cells-that-ran-earlier-tldr)). Then it reads frames until the socket closes, and waits [`backoff`](../../src/trail/jupyter/kernels.py#L144), 2.0 seconds, before it connects again. It loops while the kernel is still in `Kernels.data`. A binary frame carries buffers, such as a widget's, and no execution, so only text frames reach `receive`. The channel is the same one JupyterLab uses, so Trail receives the IOPub broadcast of every client of the kernel. It does not receive other clients' shell replies.

#### `Execution`

[`Execution`](../../src/trail/jupyter/kernels.py#L89) holds what Trail has seen of one request:

| Field | Meaning |
|---|---|
| [`request_id`](../../src/trail/jupyter/kernels.py#L93) | The request's `msg_id`, the parent id of every message of the run. |
| [`started`](../../src/trail/jupyter/kernels.py#L95) | The kernel's time of the `busy` status. |
| [`code`](../../src/trail/jupyter/kernels.py#L97), [`execution_count`](../../src/trail/jupyter/kernels.py#L99) | From `execute_input`. |
| [`submitted`](../../src/trail/jupyter/kernels.py#L101) | The kernel's time of `execute_input`. A deferred `started` record keeps it as its time. |
| [`outputs`](../../src/trail/jupyter/kernels.py#L103), [`error`](../../src/trail/jupyter/kernels.py#L105) | Counts of output messages by kind, and the last `ename` and `evalue`. |
| [`event`](../../src/trail/jupyter/kernels.py#L107), [`timer`](../../src/trail/jupyter/kernels.py#L109) | The `started` event once logged, and the pending timer that will log it. |

#### `Kernels.receive`

[`receive`](../../src/trail/jupyter/kernels.py#L385) turns one text frame into at most one event. The first excerpt filters:

```python
def receive(
        self,
        kernel: Kernel,
        text: str,
) -> ExecutionEvent | None:
    """Read one kernel message. Returns the event it logged, or None."""
    try:
        message = json.loads(text)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    header = message.get('header') or {}
    parent = message.get('parent_header') or {}
    ...
    if message.get('channel') != 'iopub':
        return None
    ...
    if parent.get('msg_type') != 'execute_request':
        return None
    request_id = parent.get('msg_id')
    if not isinstance(request_id, str):
        return None
```

A frame that is not a JSON object, that is not on the `iopub` channel, or whose parent is not an `execute_request` is ignored. The elided parts handle the history reply and a changed kernel session ([recovering cells that ran earlier](#recovering-cells-that-ran-earlier-tldr), [Kernel events](11-15-3-kernel-event.md#restarts-tldr)). The second excerpt starts an `Execution`:

```python
    kind = header.get('msg_type')
    content = message.get('content') or {}
    at = moment(header)
    execution = kernel.executions.get(request_id)
    if execution is None:
        # an execution whose start was missed is not logged
        if (
            kind != 'status'
            or content.get('execution_state') != 'busy'
        ):
            return None
        execution = Execution(request_id=request_id, started=at)
        kernel.executions[request_id] = execution
        return None
```

[`moment`](../../src/trail/jupyter/kernels.py#L30) reads the kernel's stamp from `header.date`, treats a naive stamp as UTC, and uses the arrival time if the stamp is missing or invalid. Only a `busy` status creates an `Execution`. If Trail connects in the middle of a run, the run's later messages find no `Execution` and are dropped. The history request recovers the input of such a run instead.

The third excerpt handles what a known execution receives:

```python
    if kind == 'execute_input':
        execution.code = str(content.get('code', ''))
        count = content.get('execution_count')
        if type(count) is int:
            execution.execution_count = count
        execution.submitted = at
        if request_id in self.requests:
            out = self.start(kernel, execution)
            return out
        ...
    if kind in OUTPUTS:
        execution.outputs[kind] = execution.outputs.get(kind, 0) + 1
        return None
    if kind == 'error':
        execution.error = {
            'ename': str(content.get('ename', '')),
            'evalue': str(content.get('evalue', '')),
        }
        return None
    if (
        kind == 'status'
        and content.get('execution_state') == 'idle'
    ):
        del kernel.executions[request_id]
        if execution.submitted is None:
            return None
        self.start(kernel, execution)
        if execution.error is None:
            verb = 'completed'
        else:
            verb = 'errored'
        out = self.log(kernel, execution, verb, at)
        return out
    return None
```

| Message | What `receive` does |
|---|---|
| `status`, `busy`, no `Execution` yet | Creates the `Execution`. Logs nothing. |
| `execute_input` | Stores the code, the count and `submitted`. Logs `started` now if the page's message is known, otherwise defers it ([joining the two reports](#joining-the-two-reports-tldr)). |
| `stream`, `display_data`, `execute_result`, `update_display_data` | Counts it under its kind in `outputs`. These kinds are listed in [`OUTPUTS`](../../src/trail/jupyter/kernels.py#L27). |
| `error` | Stores the error name and value. The traceback is not stored. |
| `status`, `idle` | Removes the `Execution`. Logs `started` if that has not happened, then logs `completed`, or `errored` if an `error` was seen. |

An `idle` for an `Execution` that never saw `execute_input` logs nothing. [`test_a_missed_start_and_other_messages_are_ignored`](../../tests/jupyter/test_kernels.py#L210) covers the ignored frames.

### Joining the two reports ([TLDR](#joining-the-two-reports-deep-dive))

This section covers how the page's message and the kernel's messages meet. [`Request`](../../src/trail/jupyter/kernels.py#L77) is a frozen dataclass of what the page said about one request: `kernel_id`, and `document_path` and `cell_id` when present.

#### `Kernels.request`

[`request`](../../src/trail/jupyter/kernels.py#L159) is called from the consumer for each `notebook/v1` message:

```python
def request(
        self,
        server: Server,
        record: Message,
) -> None:
    """
    Read a message in which a page names the cell and notebook of an execute request.
    """
    obj = record.data.get('object')
    if not isinstance(obj, dict):
        raise TypeError('Expected an object')
    request_id = obj.get('request_id')
    kernel_id = obj.get('kernel_id')
    if (
        not isinstance(request_id, str)
        or not isinstance(kernel_id, str)
    ):
        raise TypeError('Expected a request and a kernel id')
    document_path = obj.get('document_path')
    if not isinstance(document_path, str):
        document_path = None
    cell_id = obj.get('cell_id')
    if not isinstance(cell_id, str):
        cell_id = None
    self.requests[request_id] = Request(
        kernel_id=kernel_id,
        document_path=document_path,
        cell_id=cell_id,
    )
    while len(self.requests) > self.request_limit:
        self.requests.popitem(last=False)
    record.src_path = document_path or ''
    record.outcome = 'used'
    kernel = self.data.get((server.json.url, kernel_id))
    if kernel is None:
        return
    execution = kernel.executions.get(request_id)
    if (
        execution is not None
        and execution.submitted is not None
    ):
        self.start(kernel, execution)
```

The consumer calls it from [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) when the schema id equals [`NOTEBOOK`](../../src/trail/jupyter/consumer.py#L24). The source comment says why: the page's request names the cell of a kernel's execution. A `TypeError` is caught by `receive`, which marks the message `skipped` with the reason `invalid payload`. [`Kernels.requests`](../../src/trail/jupyter/kernels.py#L157) keeps the last 10000 requests ([`request_limit`](../../src/trail/jupyter/kernels.py#L148)). It never removes a request when its execution ends. The `Message` gets the page's path as sent, relative to the server root, as its [`src_path`](../../src/trail/jupyter/event.py#L58). This differs from the map messages, whose `src_path` is absolute. The comment on [`Message.src_path`](../../src/trail/jupyter/message.py#L41) states the exception: an absolute file path, or the page's own path for notebook messages. **Observed:** a page request for `analysis.ipynb` gave a `Message` with `src_path: analysis.ipynb`.

The last lines are for a request that arrives late: if the kernel's `execute_input` is already in, the page's message starts the execution now.

#### The two arrival orders

[`start`](../../src/trail/jupyter/kernels.py#L554) logs `started` once, from `submitted`, and cancels a pending timer:

```python
def start(
        self,
        kernel: Kernel,
        execution: Execution,
) -> ExecutionEvent | None:
    """Log the started event of an execution, once."""
    if execution.timer is not None:
        execution.timer.cancel()
        execution.timer = None
    if (
        execution.event is not None
        or execution.submitted is None
    ):
        return None
    out = self.log(kernel, execution, 'started', execution.submitted)
    execution.event = out
    return out
```

When `execute_input` arrives and the request is not known, `receive` defers the start. If no event loop is running, which only happens in a synchronous call such as a test, it starts at once. Otherwise it sets `execution.timer = loop.call_later(self.wait, self.start, kernel, execution)`. [`wait`](../../src/trail/jupyter/kernels.py#L146) is 0.5 seconds. The source comment says it is how long a `started` record waits for the page to name its cell.

| Order | What happens |
|---|---|
| The page first | `request` stores the message. At `execute_input`, the request is known, so `started` is logged at once with the cell id. |
| The kernel first, the page within 0.5 s | `execute_input` sets the timer. The page's message reaches `request`, which calls `start`, which cancels the timer. `started` carries the cell id. |
| The kernel first, no message from the page | The timer fires after 0.5 s and logs `started` without a cell id. If the run ends first, the `idle` message starts it just before the end is logged. |

**Observed:** in a run with an event loop, the first request logged nothing at its `execute_input`, and its `started` appeared as soon as the page's message arrived, with `cell_id: cell-A`. A second request, with no message from the page, logged nothing at its `execute_input` and had logged `started`, with no `cell_id`, when checked 0.7 s later. A third request ran for 19 ms with no message from the page. Nothing was logged at its `execute_input`, and `idle` logged `started` and `completed` together, dated at the kernel's times of `execute_input` and `idle`. [`test_a_started_record_waits_briefly_for_the_page`](../../tests/jupyter/test_kernels.py#L273) covers the first two orders.

#### `Kernels.log` and `Kernels.resolve`

[`log`](../../src/trail/jupyter/kernels.py#L601) builds the event for all three verbs:

```python
def log(
        self,
        kernel: Kernel,
        execution: Execution,
        verb: str,
        at: datetime,
) -> ExecutionEvent:
    """Log an execution event at the given time."""
    # the page that sent the request names its document, even on a kernel shared by several
    request = self.requests.get(execution.request_id)
    if (
        request is not None
        and request.document_path
    ):
        src_path, asset = self.resolve(kernel, request.document_path)
    else:
        src_path, asset = self.resolve(kernel)
    data: dict[str, Any] = {
        'verb': verb,
        'kernel_id': kernel.id,
        'kernel_session': kernel.session,
        'request_id': execution.request_id,
        'execution_count': execution.execution_count,
        'code': execution.code,
        'paths': list(kernel.paths),
        'started_at': execution.started.isoformat(),
    }
    if request is not None:
        data['cell_id'] = request.cell_id
        data['document_path'] = request.document_path
    if verb != 'started':
        data['ended_at'] = at.isoformat()
        data['outputs'] = dict(execution.outputs)
        data['error'] = execution.error
        if execution.event is not None:
            data['started_id'] = execution.event.id
    event = ExecutionEvent(
        schema_id=ExecutionEvent.schema,
        data=data,
        server=kernel.server.json.url,
        src_path=src_path,
        timestamp=at,
    )
    event.entry = asset
    event.apply(self._trail)
    return event
```

The three records of one run differ as follows:

| Record | `timestamp` | `data` beyond the common keys |
|---|---|---|
| `started` | The kernel's time of `execute_input` | `cell_id` and `document_path` if the page's message is known |
| `completed` or `errored` | The kernel's time of `idle` | `ended_at`, `outputs`, `error`, and `started_id`, the id of the `started` record |

The common keys are `verb`, `kernel_id`, `kernel_session`, `request_id`, `execution_count`, `code`, `paths` and `started_at`, the kernel's time of `busy`. Every record stores `code`. Only a `started` record prints it.

[`resolve`](../../src/trail/jupyter/kernels.py#L572) finds the notebook:

```python
def resolve(
        self,
        kernel: Kernel,
        path: str | None = None,
) -> tuple[str, Asset | None]:
    """The absolute path and asset of the notebook a record is about."""
    if path is None:
        # a kernel names its document only when that document is its one session
        if (
            len(kernel.paths) != 1
            or len(kernel.documents) != 1
        ):
            return '', None
        path = kernel.documents[0]
    located = kernel.server.locate(path)
    if located is None:
        return '', None
    ...
    out = (str(located), asset)
    return out
```

The elided lines look up the asset with `trail.assets.get`. If the file exists inside the project but is not yet an asset, they scan it in, as [`Consumer.resolve`](../../src/trail/jupyter/consumer.py#L238) does for a map. [`Server.locate`](../../src/trail/jupyter/server.py#L298) joins the path to the server's root and refuses paths that climb out of it.

| The page's message | The kernel's sessions | `src_path` and `entry` |
|---|---|---|
| Names a notebook | Any | The notebook's absolute path and its asset |
| None, or no path | One session, and it is a notebook | That notebook |
| None, or no path | Several sessions, or one that is not a file | Empty path and no entry. The `paths` row lists the session paths. |
| Names a console, such as `console-9` | | An absolute path to a file that does not exist, and no entry |

**Observed:** all four rows in a scenario run. The kernel shared by `analysis.ipynb` and `console-1` logged `started` and `completed` with no `src_path` and `paths: ['analysis.ipynb', 'console-1']`. A request whose page named `analysis.ipynb` on the same kernel logged both records against the notebook with its cell id. A request that named `console-9` gave a `src_path` under the project root and no entry. [`test_a_shared_kernel_names_no_notebook`](../../tests/jupyter/test_kernels.py#L192) and [`test_the_page_names_the_cell_and_notebook_of_a_shared_kernel`](../../tests/jupyter/test_kernels.py#L245) cover the first two.

The `src_path` is the located path even when no asset was found. **Deduction:** a notebook outside the project, or one a rule leaves untracked, still prints its path with no entry.

### Recovering cells that ran earlier ([TLDR](#recovering-cells-that-ran-earlier-deep-dive))

This section covers how Trail gets the inputs of runs it did not watch. The [IPython history report](../report/11-3-1-ipython-history.md) proposed it as an optional follow-up to live capture, and the code in `listen` carries the comment `trail: execution - ask for the inputs that ran before Trail was listening`.

#### The request

[`history_request`](../../src/trail/jupyter/kernels.py#L47) builds a `history_request` message for the kernel's shell channel:

```python
def history_request(identifier: str) -> str:
    """Text of a request for the inputs of the kernel's current session."""
    # the inputs of the kernel's current session, as typed
    header = {
        'msg_id': identifier,
        'msg_type': 'history_request',
        'session': uuid4().hex,
        'username': 'trail',
        'version': '5.3',
        'date': datetime.now(UTC).isoformat(),
    }
    message = {
        'header': header,
        'parent_header': {},
        'metadata': {},
        'content': {
            'output': False,
            'raw': True,
            'hist_access_type': 'range',
            'session': 0,
            'start': 1,
            'stop': None,
        },
        'channel': 'shell',
    }
    out = json.dumps(message)
    return out
```

`session: 0` means the kernel's current history session, and `raw: True` asks for the input as typed. [`listen`](../../src/trail/jupyter/kernels.py#L343) stores the request's id in [`Kernel.history`](../../src/trail/jupyter/kernels.py#L132) and sends it on each connection, so a reconnect asks again.

#### `Kernels.recover`

The reply is recognized in `receive` by its channel, its type and a parent id equal to `kernel.history`. `receive` clears `kernel.history`, so a second reply to the same request is ignored, and passes the reply to [`recover`](../../src/trail/jupyter/kernels.py#L490):

```python
def recover(
        self,
        kernel: Kernel,
        reply: dict[str, Any],
) -> list[ExecutionEvent]:
    """Log the inputs a kernel ran before Trail listened."""
    # history keeps no time, outcome, notebook or cell. The kernel's current documents are
    # kept as context, but they do not show which one ran an input
    session = (reply.get('header') or {}).get('session')
    entries = (reply.get('content') or {}).get('history') or []
    if not isinstance(session, str):
        return []
    logged = {
        event.execution_count
        for event in self._trail.events
        if isinstance(event, ExecutionEvent)
        and event.data.get('kernel_session') == session
    }
    running = {
        execution.execution_count
        for execution in kernel.executions.values()
    }
    ...
```

`logged` holds the counts already in the log for this kernel run, whether they were seen live or recovered earlier. `running` holds the counts of runs in progress. Both are skipped. For each remaining entry, `recover` stores a record:

```python
            data = {
                'verb': 'recovered',
                'kernel_id': kernel.id,
                'kernel_session': session,
                'request_id': None,
                'execution_count': count,
                'code': str(entry[2]),
                'paths': list(kernel.paths),
                'cell_id': None,
            }
            event = ExecutionEvent(
                schema_id=ExecutionEvent.schema,
                data=data,
                server=kernel.server.json.url,
            )
            event.apply(self._trail)
```

The event has no `src_path`, no entry and no timestamp argument, so its timestamp is the moment of recovery. `paths` lists every session path of the kernel as context. The code comment says these paths do not show which document ran an input. The consequences match the report: a recovered record has no notebook, and history cannot say when the input ran or how it ended.

**Observed:** a scenario kernel had run execution 2 live. A history reply listed the counts 1, 2 and 3. Trail logged `recovered` records for 1 and 3 only, and `kernel.history` was empty afterwards.

```text
0. ExecutionEvent
    verb: recovered
    execution_count: 1
    code: a = 1 (+2 lines)
    paths: ['analysis.ipynb']
    kernel_id: K1
1. ExecutionEvent
    verb: recovered
    execution_count: 3
    code: c
    paths: ['analysis.ipynb']
    kernel_id: K1
```

The listing omits the `id`, `timestamp`, `schema_id` and `server` rows. [`test_history_recovers_the_inputs_trail_missed`](../../tests/jupyter/test_kernels.py#L322) covers this, and also that a reply to another client's request is ignored, that a repeated reply adds nothing, and that a later Trail finds the recovered records in the log and adds none. [`test_executions_before_trail_listened_are_recovered`](../../tests/jupyter/test_server_live.py#L317) covers it against a real kernel, and it passed in an environment with `jupyter_server`.

**Deduction:** a reconnect after a dropped websocket asks again. A run that finished during the gap would then be recovered, without its outputs or timing. A run that was in progress when the socket dropped stays in `kernel.executions`, so its count is skipped as running and it never gets an end.

### The record class ([TLDR](#the-record-class-deep-dive))

This section introduces the class that holds a cell record. [`ExecutionEvent`](../../src/trail/jupyter/event.py#L526) lives in `trail.jupyter.event`. `Kernels` builds it, and the console and [`trail.events.select`](../../src/trail/collection.py#L75) read it. It extends [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), which holds `schema_id`, `data`, `server`, `src_path` and `entry`, and [`Event`](../../src/trail/event.py#L59) before it.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class ExecutionEvent(JupyterEvent):
    """
    One execution of code in a kernel, built by Kernels from the kernel's messages. The verbs are
    started, completed, errored and recovered.
    """

    # trail: execution - a kernel's IOPub messages for one request, as Trail observed them.
    schema: ClassVar[str] = 'trail/notebook-execution/v1'
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'execution_count', 'kernel_id', 'request_id', 'status', 'code', 'cell_id',
    )
    ...
```

The class registers itself under `trail/notebook-execution/v1` through [`JupyterEvent.__init_subclass__`](../../src/trail/jupyter/event.py#L62). The schema id is Trail's own. The [IPython history report](../report/11-3-1-ipython-history.md) called it "a proposed internal schema label, not an existing Jupyter schema". Kernel and recovery code build the event directly. The consumer's registry lookup is never used for it, because [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) handles only messages under the JupyterGIS prefix. [`ExecutionEvent.is_shared`](../../src/trail/jupyter/event.py#L534) is `False`. Nothing in `src` reads it. **Deduction:** it marks the event as one observer's view of a kernel, for the duplicate handling that [`trail-todo: origin`](../../src/trail/jupyter/event.py#L317) defers.

#### The properties

| Name | Reads | Meaning |
|---|---|---|
| [`execution_count`](../../src/trail/jupyter/event.py#L540) | `data['execution_count']` | The kernel's count of the run. It restarts at 1 when the kernel restarts. |
| [`ExecutionEvent.kernel_id`](../../src/trail/jupyter/event.py#L545), [`request_id`](../../src/trail/jupyter/event.py#L550) | `data` | The kernel's id, and the request's `msg_id`. |
| [`code`](../../src/trail/jupyter/event.py#L555) | `data['code']` | The code as sent. Empty if missing. |
| [`cell_id`](../../src/trail/jupyter/event.py#L560) | `data['cell_id']` | The page's cell id, or `None`. |
| [`status`](../../src/trail/jupyter/event.py#L565) | the verb | `ok` for `completed`, `error` for `errored`, else `None`. |
| [`duration`](../../src/trail/jupyter/event.py#L574) | `started_at`, `ended_at` | Seconds from the kernel's `busy` to its `idle`. `None` if either is missing. |

```python
@property
def duration(self) -> float | None:
    """Seconds from start to end, if both are known."""
    started = self.data.get('started_at')
    ended = self.data.get('ended_at')
    if (
        not isinstance(started, str)
        or not isinstance(ended, str)
    ):
        return None
    delta = datetime.fromisoformat(ended) - datetime.fromisoformat(started)
    out = delta.total_seconds()
    return out
```

The duration is the kernel's own span from `busy` to `idle`. It is not the difference between the `started` and closing records' timestamps, since a `started` record is dated at `execute_input`. **Observed:** in the captured run `busy` and `execute_input` are less than a millisecond apart, so the two measures agree. The `errored` record printed `0.089 s`, and its timestamp is 0.089 s after the timestamp of its `started` record.

#### Printing

[`ExecutionEvent._repr_items`](../../src/trail/jupyter/event.py#L587) yields the rows the console prints, after the [`JupyterEvent` rows](../../src/trail/jupyter/event.py#L107) (`id`, `timestamp`, `schema_id`, `server`, `src_path` and `verb`):

```python
def _repr_items(self) -> Iterator[tuple[str, object]]:
    yield from JupyterEvent._repr_items(self)
    if self.execution_count is not None:
        yield 'execution_count', self.execution_count
    if self.verb in ('started', 'recovered'):
        lines = self.code.splitlines() or ['']
        first = lines[0]
        if len(first) > 72:
            first = f'{first[:69]}...'
        if len(lines) > 1:
            first = f'{first} (+{len(lines) - 1} lines)'
        yield 'code', first
    duration = self.duration
    if duration is not None:
        yield 'duration', f'{duration:.3f} s'
    outputs = self.data.get('outputs')
    if outputs:
        yield 'outputs', ', '.join(
            f'{kind} {count}'
            for kind, count in outputs.items()
        )
    error = self.data.get('error')
    if isinstance(error, dict):
        yield 'error', f"{error.get('ename')}: {error.get('evalue')}"
    if self.cell_id is not None:
        yield 'cell_id', self.cell_id
    if not self.src_path:
        paths = self.data.get('paths')
        if paths:
            yield 'paths', paths
    yield 'kernel_id', self.kernel_id
```

Only the first line of the code is shown, cut at 72 characters, with the count of further lines. The `paths` row appears only when the record has no notebook. The console adds an `entry` row last, through [`Renderer.parameters`](../../src/trail/cli/render.py#L111). [`Renderer.verb`](../../src/trail/cli/render.py#L254) returns the verb for the heading, and [`VERB_STYLES`](../../src/trail/cli/theme.py#L46) colors it. It has a style for `recovered` only. The verbs `started`, `completed` and `errored` fall back to the default `event` style, so a failed run is not colored differently from a successful one. [`test_a_captured_run_is_logged_per_execution`](../../tests/jupyter/test_kernels.py#L48) checks `Renderer.verb` for `errored`.

#### What each record looks like

**Observed results.** A scenario run, at the checkouts in [How this was checked](#how-this-was-checked-tldr), replayed [`iopub.json`](../../tests/jupyter/fixtures/iopub.json) through `Kernels.receive`. The fixture holds the IOPub messages of a real kernel: four runs, a restart, and one more run. The run sent the page's message for each request first, naming the cell `cell-1` to `cell-5`. The listings omit the `id`, `schema_id` and `server` rows. The `12` before a time is the day, which `fmt.clock` adds when the date is not today.

A run with outputs:

```text
3. ExecutionEvent
    timestamp: 12 10:33:19.981
    src_path: analysis.ipynb
    verb: completed
    execution_count: 2
    duration: 0.010 s
    outputs: stream 1, execute_result 1
    cell_id: cell-2
    kernel_id: K1
```

A failing run. The start prints the code, and the end prints the error:

```text
4. ExecutionEvent
    timestamp: 12 10:33:21.189
    src_path: analysis.ipynb
    verb: started
    execution_count: 3
    code: 1 / 0
    cell_id: cell-3
    kernel_id: K1
5. ExecutionEvent
    timestamp: 12 10:33:21.278
    src_path: analysis.ipynb
    verb: errored
    execution_count: 3
    duration: 0.089 s
    error: ZeroDivisionError: division by zero
    cell_id: cell-3
    kernel_id: K1
```

A run on a kernel shared with a console, without the page's message, has no notebook:

```text
0. ExecutionEvent
    verb: started
    execution_count: 1
    code: x = 1
    paths: ['analysis.ipynb', 'console-1']
    kernel_id: K1
```

The whole captured run gave ten records. The restart is a change of the kernel's session in the broadcast, and the count began again at 1:

| Records | `execution_count` | `cell_id` |
|---|---|---|
| `started`, `completed` | 1 | `cell-1` |
| `started`, `completed` | 2 | `cell-2` |
| `started`, `errored` | 3 | `cell-3` |
| `started`, `completed` | 4 | `cell-4` |
| `started`, `completed` | 1, after the restart | `cell-5` |

**Observed in a live run:** a real Jupyter Server and a real `python3` kernel gave the same shape. A cell with output completed in 0.011 s, `1 / 0` errored in 0.087 s, the kernel was restarted through the server's API, and a cell after the restart began again at count 1. [Kernel events](11-15-3-kernel-event.md#restarts-tldr) shows the session records of that run.

#### Selecting

The names in [`ExecutionEvent.selectable`](../../src/trail/jupyter/event.py#L535) are the ones the console offers as completions, through [`attributes`](../../src/trail/collection.py#L20). A query can read any other attribute too, because [`Select.held`](../../src/trail/select.py#L264) reads whatever the record has, or a path into `data`. [`Select.equals`](../../src/trail/select.py#L241) compares a string exactly. There is no substring match.

| Query | Selects | Observed on the captured run |
|---|---|---|
| `events cls=ExecutionEvent` | every cell record | 10 records |
| `events cls=ExecutionEvent verb=errored` | failed runs | 1 record |
| `events cls=ExecutionEvent status=ok` | runs that completed | 4 records |
| `events cls=ExecutionEvent cell_id=cell-3` | the records of one cell | 2 records, the start and the end |
| `events cls=ExecutionEvent duration>1` | runs longer than a second | 1 record |
| `events cls=ExecutionEvent code='1 / 0'` | records of that code | 2 records, because every record stores the code |
| `events cls=ExecutionEvent data.error.ename=ZeroDivisionError` | runs that raised that error | 1 record |

[`test_the_page_names_the_cell_and_notebook_of_a_shared_kernel`](../../tests/jupyter/test_kernels.py#L245) checks the `cell_id` selection.

### Storage and replay ([TLDR](#storage-and-replay-deep-dive))

This section covers what the log keeps for a cell record and how it is rebuilt. The log is one JSON line per record in `events.jsonl`. The writing and reading paths are shared with the other families and are described in [Layer events](11-15-1-layer-event.md#storage-and-replay-tldr). This section covers only what differs.

#### Writing

[`Event.to_record`](../../src/trail/event.py#L169) writes every dataclass field except the entry, then the class name, then the entry's id and type. `ExecutionEvent` has no field of its own, so its record is the common one. **Observed:** a stored record has these keys, in this order: `id`, `timestamp`, `is_accepted`, `schema_id`, `data`, `server`, `src_path`, `cls`, `entry`, `entry_type`. `entry` is the notebook asset's id, or `None` for a recovered record or a shared kernel. `src_path` is absolute. The properties and the printed rows are computed from `data` on each access.

The `data` of an `errored` record holds these keys, in this order: `verb`, `kernel_id`, `kernel_session`, `request_id`, `execution_count`, `code`, `paths`, `started_at`, `cell_id`, `document_path`, `ended_at`, `outputs`, `error` and `started_id`. The `error` holds `ename` and `evalue`. A `started` record stops at `document_path`, and a `recovered` record has the keys listed in [`recover`](#recovering-cells-that-ran-earlier-tldr).

#### Reading

[`JSONL.read`](../../src/trail/event.py#L625) replays the log when a project opens, and [`Event.from_record`](../../src/trail/event.py#L128) rebuilds each record by its stored class name. `ExecutionEvent` registers in `Event.classes` through [`Event.__init_subclass__`](../../src/trail/event.py#L108), so it is found.

Two pieces of kernel code read the replayed log. [`Kernels.recover`](../../src/trail/jupyter/kernels.py#L490) reads the counts of a kernel run from it. [`Kernels.logged_documents`](../../src/trail/jupyter/kernels.py#L302) reads the notebooks a kernel was attached to ([Kernel events](11-15-3-kernel-event.md#finding-kernels-and-their-notebooks-tldr)). A later run of Trail therefore does not record an input or an attachment twice.

**Observed results.** In the scenario run, reopening the project rebuilt all 12 execution and kernel records of the run, and every one equalled its original `to_record()`. [`test_a_captured_run_is_logged_per_execution`](../../tests/jupyter/test_kernels.py#L48) does the same for the captured run.

### Limits today ([TLDR](#limits-today-deep-dive))

These are the limits of today's cell records. Each is labeled by how it was established. The last part lists where the code differs from the [event coverage report](../../scratch/11-12-2-event-coverage.md).

**Current behavior: a notebook that no JupyterLab page has open has no cell.** The cell id comes only from the page's message, and so does the page's choice of notebook. A script that runs code on a kernel through the server's API has no page. Trail still records the run from the kernel's broadcast. It names the notebook only if the kernel serves exactly one. **Deduction:** from the emitter, which wires only widgets of the main area with a session context.

**Current behavior: a shared kernel without the page's message has no notebook.** [`resolve`](../../src/trail/jupyter/kernels.py#L572) refuses to guess when the kernel has several sessions. **Observed**, as shown under [Joining the two reports](#joining-the-two-reports-tldr).

**Current behavior: a failed emit leaves no trace.** A rejected `notebook/v1` message reaches only the browser console. Trail records the run without a cell and cannot tell that the message was lost.

**Current behavior: a restart drops the runs in progress.** The kernel's session change clears `kernel.executions`. A run that had its `started` record keeps it and gets no `completed` or `errored`. **Observed:** the run in progress at a restart had a `started` record and `kernel.executions` was empty afterwards.

**Current behavior: a started run is dated at `execute_input`.** `started` carries the kernel's time of `execute_input`, and its `started_at` holds the time of `busy`.

**Current behavior: the page's `time` is not used.** The page's clock stays on the `Message` in `session.messages`, and no cell record stores it. The records are dated with the kernel's times.

**Current behavior: recovered records have the time of recovery and no notebook, cell, outcome or duration.** See [recovering cells that ran earlier](#recovering-cells-that-ran-earlier-tldr).

**Current behavior: only the inputs of the current kernel run are recovered.** The history request uses `session: 0`. Inputs of runs before a restart are not asked for.

**Current behavior: a kernel that stops while Trail is off is never recorded as detached.** The kernel records are covered in [Kernel events](11-15-3-kernel-event.md#limits-today-tldr).

**Current behavior: the traceback is not stored.** An error keeps `ename` and `evalue`.

**Planned:** the [event coverage report](../../scratch/11-12-2-event-coverage.md) lists linking a cell to the files it wrote as remaining work, and says that "Today only the overlapping times show it". The [native file events report](../report/11-5-1-native-file-events.md) has the options. Cell records and file records carry no link today.

#### Differences from the event coverage report

The [event coverage report](../../scratch/11-12-2-event-coverage.md) says that each cell gives a `started` and a `completed` or `errored` record that "hold the code, its execution count, the cell id, the duration, output counts and the error name and message". The code agrees, with these details:

- Every record stores the code, but only `started` and `recovered` records print it. A `completed` record prints [`duration`](../../src/trail/jupyter/event.py#L574) and `outputs`, and an `errored` record prints `duration` and `error`.
- The duration and the outputs are on the closing record only.
- The report says "Python and bash kernels were tested". **Observed:** the live test for `python3` passed. The one for `bash` was skipped, because no bash kernel is installed in the checked environment.
- The report says a `started` record "waits up to half a second for the report". That is the longest wait. It ends at once when the page's message arrives, and a run that ends sooner is started at `idle`.
- The report does not mention that a kernel with exactly one notebook session is attributed without the page's message.
- The report says "Cells that ran before Trail connected are recovered from the kernel's history". The request covers the kernel's current run only.
- The report's records show `timestamp` of a `started` record and of a `completed` record. A recovered record's `timestamp` is the time of recovery, which the report does not say.

The plan in the [IPython history report](../report/11-3-1-ipython-history.md) and the code also differ, because the plan's payloads were illustrations:

| The plan's example | The code |
|---|---|
| One verb, `executed`, with `status` | Two records, `started` and `completed` or `errored`. `status` is a property of the closing verb, not a stored key. |
| A `source` key, `live` or `history` | No such key. The verb `recovered` marks history. |
| `JupyterEvent` for the example | The class [`ExecutionEvent`](../../src/trail/jupyter/event.py#L526). |
| No outputs, paths or request records | The data also holds `outputs`, `paths`, `document_path` and `started_id`. |

### How this was checked ([TLDR](#how-this-was-checked-deep-dive))

**Checkouts.**

- **Trail:** commit `25d97ba` on `stable`. `stable` is two commits ahead of `origin/stable`, so Trail links are relative to the working tree. The working tree held uncommitted docstrings and comments in `src/trail/jupyter`, which another session added while these notes were written. No code changed. Links and code excerpts were checked against those files at 15:33 on 15 November 2026, and line anchors move if the files change.
- **JupyterGIS emitter:** commit [`932c09ae`](https://github.com/hodczak/prototype-jupytergis-eventfeed/commit/932c09aef44cfce398c3c4db41af8e56d552d9fe) of `hodczak/prototype-jupytergis-eventfeed`, branch `stable`, equal to `origin/stable`. Its links are pinned to that commit.

**Observed results.**

- **Trail tests, without `jupyter_server`.** `PYTHONPATH=src .venv/bin/python -m pytest tests/jupyter/test_kernels.py tests/jupyter/test_consumer.py tests/jupyter/test_events.py tests/jupyter/test_subscription.py` gave 76 passed and 1 skipped. The skipped test is [`test_schema_copies_match_installed_eventfeed`](../../tests/jupyter/test_events.py#L289), because `jupytergis_core` is not installed in that environment.
- **Trail tests, with `jupyter_server`.** The micromamba environment `trail` has `jupyter_server` 2.21.1 and `ipykernel` 7.4.0. `pytest tests/jupyter` there gave 89 passed and 1 skipped. The skip is the `bash` case of [`test_a_real_kernel_is_followed_from_attach_to_detach`](../../tests/jupyter/test_server_live.py#L184), because no bash kernel is installed.
- **Emitter tests.** `npx jest src/eventFeed --ci --coverage=false` in `python/jupytergis_core` of the clone gave 6 suites and 49 tests passed. The clone stayed clean.
- **Schema copy.** A manual `diff` of `tests/jupyter/schemas/notebook_v1.yaml` against the emitter's [`notebook_v1.yaml`][ef-schema-notebook] showed no difference.
- **Scenario runs.** Throwaway scripts built a project in a scratch folder with `.ontrail` holding `./`, `*.jgis` and `*.ipynb`, a tracked `analysis.ipynb`, and a [`Server`](../../src/trail/jupyter/server.py#L233) as the [`server` fixture](../../tests/jupyter/conftest.py#L130) does. They sent the page's messages through `Consumer.receive` and the kernel's frames through `Kernels.receive`, with the websocket connection switched off as in [the `kernels` fixture](../../tests/jupyter/test_kernels.py#L19). The scripts were not kept in the repo.
- **Live run.** A script started a local Jupyter Server with a random token in a temporary project, created a session for `analysis.ipynb` with a `python3` kernel through the server's API, ran two cells through a second websocket client, restarted the kernel through the API, ran a third cell, and deleted the session. Trail's real `Kernels` listener recorded 6 execution records and 3 kernel session records. The page's messages were injected through `Consumer.receive`, as the live tests do.

**Not checked.** No JupyterLab page was run. Everything about the emitter comes from reading its source, its unit tests and its docs. The page's message in the live run was injected, so the path from a real notebook's execute request to Jupyter Server's event feed was not run.

[ef-events-md-requests]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#execution-requests
[ef-events-md-silent]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#silent-failures
[ef-wire-requests]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/executions.ts#L29
[ef-wire-shell]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/executions.ts#L84
[ef-attach]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/executions.ts#L64
[ef-dispose]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/executions.ts#L69
[ef-local-path]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/executions.ts#L18
[ef-plugin]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L152
[ef-plugin-wire]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L167
[ef-schema-notebook]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/event_schemas/notebook_v1.yaml
[ef-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/events.py#L14
[ef-test-requests]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/executions.spec.ts#L41
[ef-test-kernel-change]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/executions.spec.ts#L68
[ef-test-wired-once]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/executions.spec.ts#L85
