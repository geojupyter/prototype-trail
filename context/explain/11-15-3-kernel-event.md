# Kernel events

## TLDR

Unlabeled statements describe the current code, read at the checkouts in [How this was checked](#how-this-was-checked-tldr). **Observed** marks output from a run. **Deduction** marks my inference. **Planned** marks work that a plan or a `trail-todo` comment describes and the code does not do. A reason is credited to the plan, schema or comment that states it. Links to Trail code point at the working tree. Links to JupyterGIS code are pinned to a commit.

### What happens to a kernel ([Deep Dive](#what-happens-to-a-kernel-tldr))

You open a notebook, restart its kernel, or close the notebook. Trail records each as a [`KernelSessionEvent`](../../src/trail/jupyter/event.py#L624): `attached` when a kernel starts serving a notebook, `restarted` when that kernel is restarted, and `detached` when it stops serving it. There is one record per notebook, so a kernel shared by two notebooks gives two records. **Observed:** a real Jupyter Server and kernel gave `attached`, `restarted` and `detached` records for `analysis.ipynb`, with the kernel keeping its id across the restart ([what each record looks like](#what-each-record-looks-like)).

These terms are needed:

- **JupyterLab** is the browser workspace that holds notebooks and maps.
- A **notebook** is an `.ipynb` file. Trail tracks it as an asset.
- A **kernel** is the process that runs a notebook's code. A kernel has an id that stays the same when it restarts.
- A **session** is Jupyter Server's record that a notebook, or a console, uses a kernel. It holds the notebook's path and the kernel's id.
- **Jupyter Server** is the process behind JupyterLab. It lists the sessions and relays the kernels' messages.

Three steps stay distinct:

1. **You act.** You open, restart or close a notebook.
2. **The server and the kernel report, but not as events.** The server lists its sessions when asked ([what the server reports](#what-the-server-reports-tldr)). A restarted kernel's broadcast carries a new session stamp. Neither is a message that JupyterGIS or Jupyter Server sends for this purpose.
3. **Trail records.** It asks for the sessions every two seconds, compares them with what it knew, and appends a record for each notebook that gained or lost a kernel ([finding kernels and their notebooks](#finding-kernels-and-their-notebooks-tldr), [attaching and detaching](#attaching-and-detaching-tldr)). It reads each kernel's broadcast and appends a record for each notebook when the stamp changes ([restarts](#restarts-tldr)).

| Piece | Where | Role |
|---|---|---|
| [`JupyterSession.run`](../../src/trail/jupyter/session.py#L88) | `trail.jupyter.session` | Wakes every few seconds and calls the kernel code. |
| [`Kernels`](../../src/trail/jupyter/kernels.py#L139) | `trail.jupyter.kernels`, reached as [`trail.jupyter.session.kernels`](../../src/trail/jupyter/session.py#L43) | Polls the sessions, keeps a [`Kernel`](../../src/trail/jupyter/kernels.py#L113) for each one and logs the records. |
| [`KernelSessionEvent`](../../src/trail/jupyter/event.py#L624) | `trail.jupyter.event` | The record ([the record class](#the-record-class-tldr)). |

Skeleton of the Trail side, condensed from the source and not runnable as written:

```python
# trail/jupyter/session.py
class JupyterSession(Session):
    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            self.kernels.refresh()                  # one poll per subscribed server

# trail/jupyter/kernels.py
class Kernels(Node):
    async def poll(self, server) -> None:           # GET api/sessions
        self.update(server, sessions)

    def update(self, server, sessions) -> None:
        self.log_session(kernel, 'attached', path)  # or 'detached'

    def log_session(self, kernel, verb, path) -> KernelSessionEvent:
        event = KernelSessionEvent(schema_id=..., data=data, server=..., src_path=src_path)
        event.apply(self._trail)
```

### What the server reports ([Deep Dive](#what-the-server-reports-tldr))

Jupyter Server answers `GET /api/sessions` with one entry per session: its path, its type, and the kernel's id and name. The entry names the notebook and the kernel together, which is what Trail needs. Jupyter Server also publishes its own `kernel_actions` events on the feed Trail subscribes to, with the actions `start`, `interrupt`, `shutdown` and `restart`. Trail drops them. **Observed:** the schema of those events has a kernel id and name and no path, so they cannot name the notebook. **Deduction:** that is why Trail asks for the sessions instead.

```python
# trail/jupyter/kernels.py
async def poll(self, server: Server) -> None:
    url = f'{server.json.url}api/sessions'
    ...
    sessions = json.loads(response.body)
    self.update(server, sessions)
```

### Finding kernels and their notebooks ([Deep Dive](#finding-kernels-and-their-notebooks-tldr))

Trail keeps a [`Kernel`](../../src/trail/jupyter/kernels.py#L113) for each kernel id the server lists. It holds two lists. `paths` is every path a session reports for the kernel, including consoles. `documents` is the paths that name a file on the server's disk. Only documents are logged. A new notebook's kernel can start under a made-up path before the file exists, and that path stays out of `documents` until the file does. A new `Kernel` also starts from the log: the notebooks its last records left attached are its first `documents`, so a later run of Trail does not attach them again.

```python
# trail/jupyter/kernels.py
@dataclass(slots=True, kw_only=True, eq=False)
class Kernel:
    paths: tuple[str, ...] = ()        # every session path
    documents: tuple[str, ...] = ()    # the paths that name a file
    session: str = ''                  # the kernel process's message session
```

### Attaching and detaching ([Deep Dive](#attaching-and-detaching-tldr))

Each poll gives [`Kernels.update`](../../src/trail/jupyter/kernels.py#L239) the full list. For each kernel it compares the notebooks now listed with the ones held. A notebook that is new gets an `attached` record. One that is gone gets `detached`. A kernel that the list no longer has gets `detached` for each of its notebooks, and Trail forgets it. A rename shows as one `attached` for the new path and one `detached` for the old. **Observed:** a notebook with a made-up path gave no record, then one `attached` when the file existed, then one `detached` when its session closed.

```python
# trail/jupyter/kernels.py
def update(self, server, sessions) -> None:
    documents = tuple(path for path in current if path in kernel.documents or self.is_document(server, path))
    for path in documents:
        if path not in kernel.documents:
            self.log_session(kernel, 'attached', path)
    for path in kernel.documents:
        if path not in documents:
            self.log_session(kernel, 'detached', path)
```

### Restarts ([Deep Dive](#restarts-tldr))

A restart keeps the kernel's id, so the sessions list shows nothing new. The new kernel process stamps its messages with a new `session`. Trail reads that stamp in the kernel's broadcast, through the same websocket that [Cell events](11-15-2-cell-event.md#what-the-kernel-reports-tldr) describes. When the stamp differs from the one Trail holds for the kernel, it appends a `restarted` record for each of the kernel's notebooks and drops the runs in progress. The first stamp Trail sees for a kernel is not a restart. **Observed:** a real kernel, restarted through the server's API, kept its id, and its stamp changed from `162d6c97` to `8409de0d`.

```python
# trail/jupyter/kernels.py
def receive(self, kernel, text) -> ExecutionEvent | None:
    session = header.get('session')
    if isinstance(session, str) and session != kernel.session:
        if kernel.session:
            for path in kernel.documents:
                self.log_session(kernel, 'restarted', path)
            kernel.executions.clear()
        kernel.session = session
```

### The record class ([Deep Dive](#the-record-class-tldr))

[`KernelSessionEvent`](../../src/trail/jupyter/event.py#L624) keeps everything in its `data` dictionary and stores no field of its own. Its schema id, `trail/kernel-session/v1`, is Trail's own name. The properties read the kernel's id and name and the session's path. A record prints [`kernel_name`](../../src/trail/jupyter/event.py#L641) and [`KernelSessionEvent.kernel_id`](../../src/trail/jupyter/event.py#L636), and prints the path only when it found no notebook asset. A query can filter on all four.

```python
# trail/jupyter/event.py
class KernelSessionEvent(JupyterEvent):
    schema: ClassVar[str] = 'trail/kernel-session/v1'
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'kernel_id', 'kernel_name', 'path')
```

### Storage and replay ([Deep Dive](#storage-and-replay-tldr))

Each record is one JSON line in the log, with the notebook's asset id as its entry. The kernel code reads the replayed log: the last record of each notebook says whether a kernel was left attached to it. **Observed:** a later run of Trail on the same project, with one notebook closed in between, added one `detached` record and no `attached` record.

```python
# trail/jupyter/kernels.py
def logged_documents(self, server, identifier) -> tuple[str, ...]:
    """The paths whose last logged session event for a kernel is not a detach."""
```

### Limits today ([Deep Dive](#limits-today-tldr))

**Current behavior:**

- **Records are dated when Trail polled.** An `attached` or `detached` record can be up to two seconds late, and a kernel that lives for less than one poll is never seen ([finding kernels and their notebooks](#finding-kernels-and-their-notebooks-tldr)).
- **A kernel that stops without Trail watching is never detached.** The report names a kernel that stops while Trail is off. **Deduction:** the same holds when a server stops while Trail runs, and when Trail stops itself ([attaching and detaching](#attaching-and-detaching-tldr)).
- **A `restarted` record holds the old session stamp** ([restarts](#restarts-tldr)).
- **A notebook counts only if its path names a file on the server's disk.**

### How this was checked ([Deep Dive](#how-this-was-checked-tldr))

The kernel, consumer, event and subscription tests pass: 76 passed and 1 skipped. All of `tests/jupyter` passes where `jupyter_server` is installed: 89 passed and 1 skipped. **Observed:** a scenario run drove the real `Kernels` through polls and broadcasts, and a live run used a real Jupyter Server and kernel. No JupyterLab page was run, and none is needed for this family. The server and the kernel send everything Trail uses.

## Deep Dive

### What happens to a kernel ([TLDR](#what-happens-to-a-kernel-deep-dive))

This section lists every stage between opening a notebook and a record in the log. Unlike the other event families, no browser message is involved.

| # | Where | What happens | What can stop it |
|---|---|---|---|
| 1 | [`JupyterSession.run`](../../src/trail/jupyter/session.py#L88) | Every [`interval`](../../src/trail/jupyter/session.py#L26) seconds, 2.0 by default, calls [`Kernels.refresh`](../../src/trail/jupyter/kernels.py#L208). | The session is not active. |
| 2 | `Kernels.refresh` | Starts one poll for each server whose state is `subscribed` and whose last poll is done. | A server that is not subscribed. |
| 3 | [`Kernels.poll`](../../src/trail/jupyter/kernels.py#L223) | Reads `GET {url}api/sessions` with the server's token. | Any error. The poll returns and logs nothing. |
| 4 | [`Kernels.update`](../../src/trail/jupyter/kernels.py#L239) | Builds a `Kernel` for each new kernel id and compares each kernel's notebooks with the ones held. | A session without a string path or kernel id. |
| 5 | [`Kernels.log_session`](../../src/trail/jupyter/kernels.py#L648) | Builds the record for `attached` or `detached`, finds the notebook and applies it. | |
| 6 | [`Kernels.connect`](../../src/trail/jupyter/kernels.py#L338) and [`listen`](../../src/trail/jupyter/kernels.py#L343) | Opens a websocket to each new kernel and reads its broadcast. | |
| 7 | [`Kernels.receive`](../../src/trail/jupyter/kernels.py#L385) | Notices a changed session stamp and logs `restarted` for each notebook. | The first stamp of a kernel. |
| 8 | [`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) | Appends the record to `trail.events`, which writes one line to the log. | |

[`JupyterSession`](../../src/trail/jupyter/session.py#L21) creates the kernel code as [`session.kernels`](../../src/trail/jupyter/session.py#L43). [`Kernels`](../../src/trail/jupyter/kernels.py#L139) is a [`Node`](../../src/trail/node.py#L13) of the session. The same object logs a cell's records, which [Cell events](11-15-2-cell-event.md) covers. This note covers the session records.

The record classes under [`Event`](../../src/trail/event.py#L59) and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), the log and its replay are shared with the other event families. [Layer events](11-15-1-layer-event.md#the-record-classes-tldr) explains them.

### What the server reports ([TLDR](#what-the-server-reports-deep-dive))

This section covers the two sources Trail reads. The first is the server's list of sessions. The second is the kernel's broadcast.

#### The session list

[`poll`](../../src/trail/jupyter/kernels.py#L223) sends the request with the server's token and parses the answer:

```python
async def poll(self, server: Server) -> None:
    """Fetch a server's sessions and update the kernels."""
    url = f'{server.json.url}api/sessions'
    request = HTTPRequest(
        url,
        headers=server.headers(),
        follow_redirects=False,
        request_timeout=10,
    )
    try:
        response = await AsyncHTTPClient().fetch(request)
        sessions = json.loads(response.body)
    except Exception:  # noqa: BLE001
        return
    self.update(server, sessions)
```

[`Server.headers`](../../src/trail/jupyter/server.py#L280) adds the token. A failed request, a timeout and an answer that is not JSON all end the poll without a trace. The next poll tries again.

**Observed:** a real Jupyter Server 2.21.1 answered with this entry for a notebook session, with the ids shortened:

```json
{
 "id": "...",
 "path": "analysis.ipynb",
 "name": "",
 "type": "notebook",
 "kernel": {
  "id": "...",
  "name": "python3",
  "last_activity": "...",
  "execution_state": "starting",
  "connections": "..."
 },
 "notebook": {
  "path": "analysis.ipynb",
  "name": ""
 }
}
```

Trail reads three values: `path`, `kernel.id` and `kernel.name`. It does not read `type`. A console is a session too, with its own path, and it appears in the kernel's `paths` ([finding kernels and their notebooks](#finding-kernels-and-their-notebooks-tldr)). **Observed:** the kernel's id was the same before and after a restart through the API, and the session list was empty after the session was deleted.

#### Jupyter Server's own kernel events

Jupyter Server publishes `kernel_actions/v1` events when a kernel starts, is interrupted, shuts down or restarts. They arrive on the same feed as the JupyterGIS messages. [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) keeps only the JupyterGIS schemas and the contents service, so it counts these in `server.counts.other` and drops them. **Observed:** the installed schema, [`kernel_actions/v1.yaml`](/home/arstneio/micromamba/envs/trail/lib/python3.13/site-packages/jupyter_server/event_schemas/kernel_actions/v1.yaml) of jupyter_server 2.21.1, has `action`, [`KernelSessionEvent.kernel_id`](../../src/trail/jupyter/event.py#L636), [`kernel_name`](../../src/trail/jupyter/event.py#L641), `status`, `status_code` and `msg`, and no path. **Deduction:** an event without a path cannot say which notebook the kernel serves, so the session list is needed whatever the events say. The code does not state its reason for polling.

#### The kernel's broadcast

Each kernel stamps its messages with a `session` in the header. A new kernel process has a new stamp. [Cell events](11-15-2-cell-event.md#what-the-kernel-reports-tldr) describes how Trail reads the broadcast. This note uses only the stamp ([restarts](#restarts-tldr)).

### Finding kernels and their notebooks ([TLDR](#finding-kernels-and-their-notebooks-deep-dive))

This section covers how a list of sessions becomes a set of kernels, each with its notebooks. [`Kernel`](../../src/trail/jupyter/kernels.py#L113) is a dataclass in `trail.jupyter.kernels`. [`Kernels`](../../src/trail/jupyter/kernels.py#L139) keeps one for each `(server url, kernel id)` in [`Kernels.data`](../../src/trail/jupyter/kernels.py#L153).

#### `Kernel`

| Field | Meaning |
|---|---|
| [`server`](../../src/trail/jupyter/kernels.py#L117), [`id`](../../src/trail/jupyter/kernels.py#L119), [`name`](../../src/trail/jupyter/kernels.py#L121) | The server, the kernel's id, and the kernel's name from the first session that showed it, such as `python3`. |
| [`paths`](../../src/trail/jupyter/kernels.py#L123) | The notebooks and consoles the server says use this kernel, sorted. |
| [`documents`](../../src/trail/jupyter/kernels.py#L126) | The paths that name a file. Attaching and detaching are logged for these. |
| [`session`](../../src/trail/jupyter/kernels.py#L128) | The kernel process's message session. It changes when the kernel restarts. |
| [`executions`](../../src/trail/jupyter/kernels.py#L130), [`history`](../../src/trail/jupyter/kernels.py#L132) | The runs in progress, and the id of Trail's history request. [Cell events](11-15-2-cell-event.md) covers both. |
| [`task`](../../src/trail/jupyter/kernels.py#L134), [`socket`](../../src/trail/jupyter/kernels.py#L136) | The task that reads the kernel's broadcast, and its websocket. |

The source comment on `documents` says why it exists: JupyterLab starts a new notebook's kernel under a placeholder name before the file exists. The report also records it: "JupyterLab briefly starts a new notebook's kernel under a made-up name. The live run caught this." ([event coverage report](../../scratch/11-12-2-event-coverage.md)). Made-up names recorded as notebooks was one of the five bugs the live run found.

#### `Kernels.is_document`

[`is_document`](../../src/trail/jupyter/kernels.py#L326) decides whether a path names a notebook:

```python
@staticmethod
def is_document(
        server: Server,
        path: str,
) -> bool:
    """Whether the path names a file under the server root."""
    located = server.locate(path)
    is_file = (
        located is not None
        and located.is_file()
    )
    return is_file
```

[`Server.locate`](../../src/trail/jupyter/server.py#L298) joins the path to the server's root and refuses a path that climbs out of it. A placeholder is a made-up name, so no file exists at it, and it is not a document. A console's path is not a file either. A path counts as a document if its file exists on the server's disk.

#### `Kernels.logged_documents`

A `Kernel` that Trail has not held before starts from the log, so that a later run does not attach again what an earlier run attached. [`logged_documents`](../../src/trail/jupyter/kernels.py#L302) reads it:

```python
def logged_documents(
        self,
        server: Server,
        identifier: str,
) -> tuple[str, ...]:
    """The paths whose last logged session event for a kernel is not a detach."""
    verbs: dict[str, str] = {}
    for event in self._trail.events:
        if (
            isinstance(event, KernelSessionEvent)
            and event.server == server.json.url
            and event.kernel_id == identifier
            and event.path is not None
        ):
            verbs[event.path] = event.verb
    attached = [
        path
        for path, verb in verbs.items()
        if verb != 'detached'
    ]
    out = tuple(sorted(attached))
    return out
```

The last verb of each path wins, and `restarted` counts as attached. The source comment in `update` says why: an earlier run of Trail may have logged the kernel's sessions already. The report lists "Kernel attachments were recorded twice after a restart" as one of the five bugs of the live run, which this fixes. The restart in that bug is Trail's, not the kernel's.

### Attaching and detaching ([TLDR](#attaching-and-detaching-deep-dive))

This section covers how [`update`](../../src/trail/jupyter/kernels.py#L239) turns a list of sessions into records. It runs in three parts. The first part groups the sessions by kernel:

```python
def update(
        self,
        server: Server,
        sessions: list[dict[str, Any]],
) -> None:
    """
    Match the kernels to the server's sessions, logging notebooks that attach or detach.
    """
    found: dict[str, Kernel] = {}
    paths: dict[str, list[str]] = {}
    for session in sessions:
        kernel = session.get('kernel') or {}
        identifier = kernel.get('id')
        path = session.get('path')
        if (
            not isinstance(identifier, str)
            or not isinstance(path, str)
        ):
            continue
        paths.setdefault(identifier, []).append(path)
        key = (server.json.url, identifier)
        known = self.data.get(key)
        if known is None:
            # an earlier run of Trail may have logged the kernel's sessions already
            known = Kernel(
                server=server,
                id=identifier,
                name=str(kernel.get('name', '')),
                documents=self.logged_documents(server, identifier),
            )
        found[identifier] = known
```

A session with no string path or kernel id is skipped. A kernel Trail holds keeps its object. A new one is built here, with its documents from the log.

The second part compares each kernel's notebooks and logs the differences:

```python
    for identifier, kernel in found.items():
        current = tuple(sorted(paths[identifier]))
        documents = tuple(
            path
            for path in current
            if path in kernel.documents
            or self.is_document(server, path)
        )
        for path in documents:
            if path not in kernel.documents:
                self.log_session(kernel, 'attached', path)
        for path in kernel.documents:
            if path not in documents:
                self.log_session(kernel, 'detached', path)
        kernel.paths = current
        kernel.documents = documents
        key = (server.json.url, identifier)
        if key not in self.data:
            self.data[key] = kernel
            self.connect(kernel)
```

A path is a document if it was one already or if its file exists. The first condition keeps a notebook attached while its session is listed, even if the file is renamed away. A path in `documents` that was not before is attached, and one that is no longer listed is detached. A new kernel is stored and connected after its `attached` records, so the first record precedes any message from the kernel.

The third part handles a kernel the list no longer has:

```python
    for key, kernel in list(self.data.items()):
        if (
            key[0] == server.json.url
            and key[1] not in found
        ):
            for path in kernel.documents:
                self.log_session(kernel, 'detached', path)
            self.drop(key)
```

[`drop`](../../src/trail/jupyter/kernels.py#L673) closes the websocket and cancels the kernel's task and the timers of its runs. It logs nothing itself:

```python
def drop(self, key: tuple[str, str]) -> None:
    """Stop following a kernel."""
    kernel = self.data.pop(key, None)
    if kernel is None:
        return
    for execution in kernel.executions.values():
        if execution.timer is not None:
            execution.timer.cancel()
    if kernel.socket is not None:
        kernel.socket.close()
    if (
        kernel.task is not None
        and not kernel.task.done()
        and not kernel.task.get_loop().is_closed()
    ):
        kernel.task.cancel()
```

[`close`](../../src/trail/jupyter/kernels.py#L690) drops every kernel and cancels the polls. [`JupyterSession.close`](../../src/trail/jupyter/session.py#L102) calls it. It logs no `detached` record, so stopping Trail leaves each kernel attached in the log.

#### `Kernels.log_session`

[`log_session`](../../src/trail/jupyter/kernels.py#L648) builds the record for all three verbs:

```python
def log_session(
        self,
        kernel: Kernel,
        verb: str,
        path: str,
) -> KernelSessionEvent:
    """Log a kernel session event."""
    src_path, asset = self.resolve(kernel, path)
    data = {
        'verb': verb,
        'kernel_id': kernel.id,
        'kernel_name': kernel.name,
        'kernel_session': kernel.session,
        'path': path,
    }
    event = KernelSessionEvent(
        schema_id=KernelSessionEvent.schema,
        data=data,
        server=kernel.server.json.url,
        src_path=src_path,
    )
    event.entry = asset
    event.apply(self._trail)
    return event
```

[`resolve`](../../src/trail/jupyter/kernels.py#L572) is the method [Cell events](11-15-2-cell-event.md#joining-the-two-reports-tldr) describes. Here it is given the notebook's path, so it joins it to the server's root, looks up the asset, and scans the file in if it is inside the project and not yet an asset. [`src_path`](../../src/trail/jupyter/event.py#L58) is the located path, and `entry` is the asset or `None`. The event takes no timestamp, so its time is the moment `log_session` ran, which is when Trail polled or read the broadcast.

The `kernel_session` of an `attached` record is empty. Trail connects to a kernel after it logs the attachment, so it has not seen the kernel's stamp yet.

**Observed results.** A scenario run, at the checkouts in [How this was checked](#how-this-was-checked-tldr), called `update` with lists of sessions, as `poll` does. The project held tracked notebooks `a.ipynb` and `b.ipynb`. Kernel `K1` first served `a.ipynb`:

| Sessions listed | Records added | Kernel held |
|---|---|---|
| `a.ipynb` on `K1` | `attached a.ipynb` | `K1`: paths and documents `a.ipynb` |
| `a.ipynb`, `b.ipynb` and `console-1`, all on `K1` | `attached b.ipynb` | `K1`: paths all three, documents the two notebooks |
| the same, plus `8a00f17e-...` on `K9` | none | `K9`: a path, no documents |
| `Untitled.ipynb` on `K9`, after the file was created | `attached Untitled.ipynb` | `K9`: documents `Untitled.ipynb` |
| `K9` gone from the list | `detached Untitled.ipynb` | `K9` not held |
| `a.ipynb` renamed to `renamed.ipynb` on `K1` | `attached renamed.ipynb`, `detached a.ipynb` | `K1` |

[`test_sessions_attach_and_detach_notebooks`](../../tests/jupyter/test_kernels.py#L96) checks the same shapes, and [`test_a_placeholder_session_is_not_a_notebook`](../../tests/jupyter/test_kernels.py#L154) checks the placeholder. [`test_a_later_run_does_not_attach_a_logged_session_again`](../../tests/jupyter/test_kernels.py#L130) checks the log. **Observed:** with `K1` serving `renamed.ipynb` and `console-1`, a later Trail on the same project found `b.ipynb` still attached in the log and logged one `detached` for it and no `attached`.

**Deduction:** switching a notebook to another kernel gives a new kernel id for the same path. The new kernel's `attached` record comes in the second part and the old kernel's `detached` records in the third part, so the new attachment is logged first. This was not run.

### Restarts ([TLDR](#restarts-deep-dive))

This section covers how a restart is noticed. The sessions list cannot show one, because the kernel keeps its id. [`Kernels.receive`](../../src/trail/jupyter/kernels.py#L385) reads each frame of the kernel's broadcast, and the stamp is in its header:

```python
    if message.get('channel') != 'iopub':
        return None
    session = header.get('session')
    if (
        isinstance(session, str)
        and session != kernel.session
    ):
        if kernel.session:
            for path in kernel.documents:
                self.log_session(kernel, 'restarted', path)
            kernel.executions.clear()
        kernel.session = session
```

The frame must be on the `iopub` channel. If its stamp differs from [`kernel.session`](../../src/trail/jupyter/kernels.py#L128), the kernel process is new. When `kernel.session` was already set, that is a restart: Trail logs `restarted` for each notebook in `kernel.documents` and clears the runs in progress. The first stamp of a kernel sets `kernel.session` and logs nothing, since there is nothing to compare it with.

The source gives no reason for this approach. **Deduction:** the stamp is the only restart signal Trail has. The sessions list is unchanged, and the `kernel_actions` events, which do report `restart`, are dropped and have no path.

**Observed:** in a scenario run, the first frame of stamp `S1` logged nothing and set `kernel.session`. The first frame of stamp `S2` logged one `restarted` for each of the two notebooks of the kernel. **Observed in a live run:** a real `python3` kernel was restarted through the server's API. The kernel's id stayed the same. Its stamp changed from `162d6c97` to `8409de0d`, one `restarted` record followed, and the next cell began again at execution count 1.

The `restarted` record holds the old stamp. `log_session` runs before `kernel.session` is set, so `kernel_session` in the record is the process that ended. **Observed:** in a scenario run, the `restarted` record had `kernel_session: S1` and the later `detached` record had `S2`. The records of cells that run after the restart carry the new stamp.

Three other things follow from the code:

- A kernel with no notebook in `documents`, such as one that serves only a console, logs no `restarted`.
- A restart while Trail's websocket to the kernel is down is noticed at the first frame after Trail reconnects. The record is then late. **Deduction.**
- An interrupt changes nothing here, since the kernel process stays the same.

[`test_a_captured_run_is_logged_per_execution`](../../tests/jupyter/test_kernels.py#L48) covers the restart in a recorded broadcast, with the records `attached` and `restarted`.

### The record class ([TLDR](#the-record-class-deep-dive))

This section introduces the class that holds a kernel record. [`KernelSessionEvent`](../../src/trail/jupyter/event.py#L624) lives in `trail.jupyter.event`. `Kernels.log_session` builds it, and the console and [`trail.events.select`](../../src/trail/collection.py#L75) read it. It extends [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), which holds `schema_id`, `data`, `server`, `src_path` and `entry`.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class KernelSessionEvent(JupyterEvent):
    """
    A notebook attached to a kernel, detached from it, or its kernel restarted. Built by Kernels,
    not sent by the server.
    """

    # trail: execution - which notebook a kernel served, from the server's sessions.
    schema: ClassVar[str] = 'trail/kernel-session/v1'
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'kernel_id', 'kernel_name', 'path')
    ...
```

The class registers itself under `trail/kernel-session/v1` through [`JupyterEvent.__init_subclass__`](../../src/trail/jupyter/event.py#L62). The schema id is Trail's own, not a JupyterGIS schema. `Kernels` builds the event directly, and the consumer never sees this schema. [`KernelSessionEvent.is_shared`](../../src/trail/jupyter/event.py#L632) is `False`, and nothing in `src` reads it ([Cell events](11-15-2-cell-event.md#the-record-class-tldr) gives the deduction about it).

| Name | Reads | Meaning |
|---|---|---|
| [`KernelSessionEvent.kernel_id`](../../src/trail/jupyter/event.py#L636) | `data['kernel_id']` | The kernel's id. |
| [`kernel_name`](../../src/trail/jupyter/event.py#L641) | `data['kernel_name']` | The kernel's name, such as `python3`. |
| [`path`](../../src/trail/jupyter/event.py#L646) | `data['path']` | The session's path as the server reports it, relative to the server's root. |

`verb` is the common [`JupyterEvent.verb`](../../src/trail/jupyter/event.py#L92). The `data` also holds `kernel_session`, which has no property.

#### Printing

[`KernelSessionEvent._repr_items`](../../src/trail/jupyter/event.py#L650) yields the rows the console prints:

```python
def _repr_items(self) -> Iterator[tuple[str, object]]:
    yield from JupyterEvent._repr_items(self)
    for name in ('kernel_name', 'kernel_id'):
        value = getattr(self, name)
        if value:
            yield name, value
    if not self.src_path:
        yield 'path', self.path
```

The rows follow the [`JupyterEvent` rows](../../src/trail/jupyter/event.py#L107): `id`, `timestamp`, `schema_id`, `server`, `src_path` and `verb`. The `path` row appears only when the record found no file, so that the console still names the notebook. The console adds an `entry` row last, through [`Renderer.parameters`](../../src/trail/cli/render.py#L111). [`Renderer.verb`](../../src/trail/cli/render.py#L254) returns the verb for the heading, and [`VERB_STYLES`](../../src/trail/cli/theme.py#L46) colors it. It has no entry for `attached`, `restarted` or `detached`, so these headings use the default `event` style. `closed` and `deactivated` of the map records have a style, so a kernel's end is not colored like a map's.

#### What each record looks like

**Observed results.** The scenario run listed the records with `print(trail.events.select('cls=KernelSessionEvent'))`, without the `id`, `timestamp`, `schema_id` and `server` rows. The first record is the notebook's attachment:

```text
0. KernelSessionEvent
    src_path: a.ipynb
    verb: attached
    kernel_name: python3
    kernel_id: K1
```

A restart gives one record for each notebook the kernel serves. A rename gives a pair:

```text
6. KernelSessionEvent
    src_path: b.ipynb
    verb: restarted
    kernel_name: python3
    kernel_id: K1
7. KernelSessionEvent
    src_path: renamed.ipynb
    verb: restarted
    kernel_name: python3
    kernel_id: K1
```

**Observed in a live run.** A real Jupyter Server and `python3` kernel gave this log order, for `analysis.ipynb`. The kernel's records are those of [Cell events](11-15-2-cell-event.md#what-each-record-looks-like):

```text
KernelSessionEvent attached
ExecutionEvent started
ExecutionEvent completed
ExecutionEvent started
ExecutionEvent errored
KernelSessionEvent restarted
ExecutionEvent started
ExecutionEvent completed
KernelSessionEvent detached
```

#### Selecting

A query can read the names in [`KernelSessionEvent.selectable`](../../src/trail/jupyter/event.py#L633), which the console offers as completions, and any other attribute of the record, through [`Select.held`](../../src/trail/select.py#L264).

| Query | Selects | Observed |
|---|---|---|
| `events cls=KernelSessionEvent` | every kernel record | 8 records |
| `events cls=KernelSessionEvent verb=attached` | attachments | 4 records |
| `events cls=KernelSessionEvent verb=restarted` | restarts | 2 records, one per notebook |
| `events cls=KernelSessionEvent kernel_id=K1` | one kernel's records | 6 records |
| `events cls=KernelSessionEvent path=b.ipynb` | one notebook's records | 2 records, an attachment and a restart |

### Storage and replay ([TLDR](#storage-and-replay-deep-dive))

This section covers what the log keeps for a kernel record and how it is rebuilt. The writing and reading paths are shared with the other families ([Layer events](11-15-1-layer-event.md#storage-and-replay-tldr)).

[`Event.to_record`](../../src/trail/event.py#L169) writes every dataclass field except the entry. `KernelSessionEvent` has none of its own. **Observed:** a stored record has these keys, in this order: `id`, `timestamp`, `is_accepted`, `schema_id`, `data`, `server`, `src_path`, `cls`, `entry`, `entry_type`. The `data` holds `verb`, `kernel_id`, `kernel_name`, `kernel_session` and `path`:

```json
{"verb": "restarted", "kernel_id": "K1", "kernel_name": "python3", "kernel_session": "S1", "path": "a.ipynb"}
```

[`JSONL.read`](../../src/trail/event.py#L625) replays the log, and [`Event.from_record`](../../src/trail/event.py#L128) finds the class by its stored name, which `Event.__init_subclass__` registered.

The replayed log is the state Trail starts from. [`Kernels.logged_documents`](../../src/trail/jupyter/kernels.py#L302) rebuilds the attached notebooks from it. **Observed results.** Reopening the project in the scenario run rebuilt all 8 kernel records to equal records. A later run found `b.ipynb` still attached in the log and detached it. [`test_a_later_run_does_not_attach_a_logged_session_again`](../../tests/jupyter/test_kernels.py#L130) covers that.

### Limits today ([TLDR](#limits-today-deep-dive))

These are the limits of today's kernel records. Each is labeled by how it was established. The last part lists where the code differs from the [event coverage report](../../scratch/11-12-2-event-coverage.md).

**Current behavior: records are dated when Trail polled.** `log_session` takes no timestamp. An `attached` or `detached` record is dated when `update` ran, which is up to [`interval`](../../src/trail/jupyter/session.py#L26) seconds after the change, 2 seconds by default. A kernel that starts and stops between two polls is never seen. A `restarted` record is dated when the first frame of the new process arrived.

**Current behavior: a kernel that stops without Trail watching is never detached.** The report lists the case of a kernel that stops while Trail is off. **Deduction:** two more cases follow from the code. If a server stops while Trail runs, [`Kernels.refresh`](../../src/trail/jupyter/kernels.py#L208) no longer polls it, so `update` never sees the kernel gone, and [`listen`](../../src/trail/jupyter/kernels.py#L343) goes on retrying a socket that fails, counting each failure in `server.counts.skipped['kernel connection']`. And [`close`](../../src/trail/jupyter/kernels.py#L690) logs no `detached`, so stopping Trail leaves its kernels attached in the log. A later run detaches a notebook only if its kernel is listed with other sessions, as in the scenario.

**Current behavior: a failed poll leaves no trace.** [`poll`](../../src/trail/jupyter/kernels.py#L223) swallows every error. A server that cannot list sessions looks the same as one with none, until the connection state says otherwise.

**Current behavior: only notebooks with a file on the server's disk are recorded.** A session whose path names no file there, such as a console or a notebook from a remote contents manager, is in `paths` and not in `documents`. **Deduction** for the remote case.

**Current behavior: a `restarted` record holds the old session stamp.** See [restarts](#restarts-tldr).

**Current behavior: one record for each notebook.** A restart of a kernel shared by two notebooks gives two records, and one with no notebook gives none.

**Current behavior: a switch to another kernel is a detach and an attach.** **Deduction** from [`update`](../../src/trail/jupyter/kernels.py#L239). It was not run.

**Planned:** none for this family. The report lists no kernel work as remaining beyond the case of a kernel that stops while Trail is off.

#### Differences from the event coverage report

The [event coverage report](../../scratch/11-12-2-event-coverage.md) says "Kernels are recorded as attached to, restarted in and detached from each notebook". The code agrees, with these details:

- A kernel with no notebook, such as one that serves only a console, is not recorded at all.
- A `restarted` record is made for each notebook from the kernel's broadcast, so it needs Trail's websocket to the kernel. The other two verbs come from the session list.
- The report says "A kernel that stops while Trail is off is never recorded as detached". The same holds when a server stops while Trail runs and when Trail stops itself.
- The report lists "Kernel attachments were recorded twice after a restart" among the fixed bugs. The restart is Trail's. The fix is [`logged_documents`](../../src/trail/jupyter/kernels.py#L302). A kernel's own restart gives a `restarted` record and no second attachment.
- The report's records carry a `timestamp` each. For `attached` and `detached` it is the time Trail polled, and the report does not say so.

### How this was checked ([TLDR](#how-this-was-checked-deep-dive))

**Checkouts.**

- **Trail:** commit `25d97ba` on `stable`. `stable` is two commits ahead of `origin/stable`, so Trail links are relative to the working tree. The working tree held uncommitted docstrings and comments in `src/trail/jupyter`, which another session added while these notes were written. No code changed. Links and code excerpts were checked against those files at 15:33 on 15 November 2026, and line anchors move if the files change.
- **JupyterGIS emitter:** commit [`932c09ae`](https://github.com/hodczak/prototype-jupytergis-eventfeed/commit/932c09aef44cfce398c3c4db41af8e56d552d9fe) of `hodczak/prototype-jupytergis-eventfeed`, branch `stable`, equal to `origin/stable`. This family sends no message through the emitter, so no link here points at it.

**Observed results.**

- **Trail tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/jupyter/test_kernels.py tests/jupyter/test_consumer.py tests/jupyter/test_events.py tests/jupyter/test_subscription.py` gave 76 passed and 1 skipped. In the micromamba environment `trail`, which has `jupyter_server` 2.21.1 and `ipykernel` 7.4.0, `pytest tests/jupyter` gave 89 passed and 1 skipped. The skip is the `bash` case of [`test_a_real_kernel_is_followed_from_attach_to_detach`](../../tests/jupyter/test_server_live.py#L184), because no bash kernel is installed.
- **Scenario runs.** Throwaway scripts built a project in a scratch folder with `.ontrail` holding `./`, `*.jgis` and `*.ipynb`, tracked notebooks, and a [`Server`](../../src/trail/jupyter/server.py#L233) as the [`server` fixture](../../tests/jupyter/conftest.py#L130) does. They called `Kernels.update` with lists of sessions and `Kernels.receive` with frames, with the websocket connection switched off as in [the `kernels` fixture](../../tests/jupyter/test_kernels.py#L19). They then reopened the project. The scripts were not kept in the repo.
- **Live run.** A script started a local Jupyter Server with a random token in a temporary project, created a session for `analysis.ipynb` with a `python3` kernel through the server's API, ran cells through a websocket client, restarted the kernel through the API, and deleted the session. Trail's real `Kernels` listener recorded the three kernel records and six cell records listed above. A second script printed the server's answer to `GET /api/sessions`.

**Not checked.** Nothing was run in a browser. The switch of a notebook to another kernel, a server that stops while Trail runs, and a remote contents manager were not run, and their statements are deductions from the code.
