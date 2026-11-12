# IPython history for Trail

IPython history can give Trail the code submitted for execution and its execution order, including reruns. The correction to Min's description is that **a history session normally corresponds to a kernel run, not a notebook file**. Restarting a notebook's kernel starts another history session; several frontends sharing a kernel share its history. Notebook attribution therefore needs a separate mapping. [Jupyter messaging](https://jupyter-client.readthedocs.io/en/stable/messaging.html#history)

## What the magic provides

IPython *magics* are commands handled by IPython: `%name` is a line magic; `%%name` is a cell magic. `%history` displays history that IPython already collects; running it does not enable recording.

| Command | Result |
| --- | --- |
| `%history -n` | Current session's inputs with execution numbers. |
| `%history -n 243/1-5` | Inputs 1–5 from history session 243, inclusive. |
| `%history -n ~1/` | The preceding numbered history session; potentially another notebook's kernel. |
| `%history -g '*read_file*'` | Search stored inputs across sessions. |

An input can be a whole multiline cell. History shows submitted source by default; `-t` shows its transformed Python form. `-o` requests available outputs. [Magic reference](https://ipython.readthedocs.io/en/stable/interactive/magics.html#magic-history)

Inputs normally persist in the profile's SQLite database, commonly `~/.ipython/profile_default/history.sqlite`. The location is configurable, and storage can be disabled or in memory. IPython's `HistoryManager.get_range(...)` retrieves inputs programmatically; `HistoryAccessor(hist_file=...)` reads a database from another process. [History API](https://ipython.readthedocs.io/en/stable/api/generated/IPython.core.history.html)

## How Trail could use it

Trail's [JupyterSession](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/session.py:19) manages server discovery and subscriptions. Its [Consumer](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:49) handles JupyterGIS and Contents events. Execution history would add notebook code to that workflow record.

**Priority.** Capture new executions through IOPub, the kernel's existing broadcast channel. `execute_input` carries submitted code and the execution number. Related messages report output, errors, and busy/idle status. Group them by `parent_header.msg_id`. An idle message marks completion. The requesting frontend receives the explicit result status in `execute_reply`. Notebook and cell attribution require a separate association. Keep history retrieval as an optional follow-up for executions Trail missed.

> What would IPython history add if Trail already listened to live kernel messages? Is recovering earlier executions its main use?

> **Codex:** Yes. Live kernel messages supply the code, execution number, outputs, errors, and execution boundaries as they happen. IPython history adds access to retained inputs and their order, including transformed source. Its session numbers usually identify kernel runs and cannot establish notebook origin on their own. Prioritize live capture and defer history recovery until Trail needs to fill gaps.

1. **Associate tracked notebooks with kernels.** Query each connected server's `GET /api/sessions`, which returns document paths and kernel information. For example, `analysis.ipynb → kernel K` tells Trail which kernel to query for that notebook and which tracked notebook `Asset` is a candidate for the returned executions. Preserve these associations as notebooks are renamed or kernels restart. A current association alone does not prove which notebook submitted an earlier input: another notebook or console could have used the same kernel. These server session UUIDs are distinct from IPython's integer history session numbers. [Server API](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html#get--api-sessions)

> WHat does associating tracked notebooks with kernels do for us?

> **Codex:** It connects Trail's file-based model to the kernel-based history: it identifies the kernel to query and the notebook asset an execution may belong to. I expanded the text to distinguish that association from proof of origin. Shared kernels and earlier executions still require a recorded notebook/cell association.

2. **Capture live executions.** Capture notebook identity, cell identity, start/end times, and success or error. A Python kernel extension can use `pre_run_cell` and `post_run_cell`; the latter supplies the execution count and errors. A cell ID is available when supplied by the frontend. The collector must add timestamps at the callbacks and obtain notebook attribution separately. [IPython callbacks](https://ipython.readthedocs.io/en/stable/config/callbacks.html)

   | IOPub message | What Trail can get |
   |---|---|
   | `execute_input` | Submitted code and execution number |
   | `status` | `busy` and `idle` signals that bracket request processing |
   | `stream` | Text written to stdout or stderr |
   | `execute_result` | The cell’s expression result, when emitted |
   | `display_data` | Explicit displays such as plots, images, and HTML |
   | `error` | Exception type, message, and traceback |

   Group these messages by `parent_header.msg_id`, the originating execution request ID. The `idle` signal marks completion of request processing. The requesting frontend gets the explicit success or failure status in `execute_reply` on the shell channel. [IOPub protocol](https://jupyter-client.readthedocs.io/en/stable/messaging.html#messages-on-the-iopub-pub-sub-channel)

   For notebook and cell attribution, a frontend observer can supplement IOPub: it knows the notebook and can observe the reply to its own execution request. A separate Trail connection must not expect to receive other clients' shell replies. The event examples below distinguish a completed live execution from an input recovered through history. [Shell request/reply routing](https://jupyter-client.readthedocs.io/en/stable/messaging.html#introduction)

3. **Optional follow-up: recover missed executions from history.** Send a `history_request` with `hist_access_type='range'`, `session=0`, and `raw=True` for the current run. Each returned execution number and source can become a recovered execution event. Match it to any previously observed live execution by server, the kernel's message-header `session` UUID, and execution number; the UUID changes on kernel restart. For live messages, `parent_header.msg_id` links outputs and status messages to their originating request. A frontend observer must separately associate that request with its notebook and cell. Historical entries do not retain request IDs. [History protocol](https://jupyter-client.readthedocs.io/en/stable/messaging.html#history), [message identity and parent headers](https://jupyter-client.readthedocs.io/en/stable/messaging.html#parent-header)

   This creates events describing code execution; it does not establish that a particular execution caused an existing filesystem or JupyterGIS event. Matching times provides correlation. Causal attribution requires additional instrumentation that records the operation and its originating execution. If notebook origin is unknown, leave the recovered event's `entry` unset. If an execution is already logged, do not import it as another execution.

> We retrieve code through the kernel protocol, how do we attribute to actual events?

> **Codex:** Each history entry can become a recovered execution event. We can join it to a recorded live execution using the kernel identity and execution number, and then use the live request's notebook/cell association. History alone cannot identify which existing file or GIS events it caused. I added these distinctions and the rule to leave unknown notebook attribution unset.

> Okay, give some example Event instances that we'd log based on the information we get

> **Codex:** Added two examples using the existing `JupyterEvent` class: a live execution with notebook identity, timing, and outcome, and a recovered input with those fields unknown. The payload and schema label are proposed; the examples do not imply that the current consumer collects notebook executions.

### Example event objects

[`JupyterEvent`](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py:32) is Trail's existing generic event container for a schema identifier and payload. The examples use it without introducing another class. `trail/notebook-execution/v1` is a proposed internal schema label, not an existing Jupyter schema. A new collector would be needed: the current consumer accepts JupyterGIS and Contents schemas only.

All values below are illustrative. `kernel_id` identifies the server's kernel; `kernel_session` identifies that kernel process and changes on restart. `request_id` identifies the execution request. `started_at` and `ended_at` are ISO timestamps in the payload; the event's `timestamp` is a Python datetime.

```python
from datetime import datetime

from trail.jupyter.event import JupyterEvent
```

For a live execution, assume `notebook_asset` is the tracked `Asset` for `/project/analysis.ipynb`, and a frontend observer has identified that notebook and its cell as the request's origin. The collector observed execution 12 complete successfully:

```python
live_data = dict(
    verb='executed',
    source='live',
    kernel_id='kernel-1',
    kernel_session='kernel-process-1',
    execution_count=12,
    request_id='request-12',
    cell_id='cell-3',
    code="frame = pd.read_csv('roads.csv')",
    started_at='2026-11-03T18:00:00+00:00',
    ended_at='2026-11-03T18:00:01+00:00',
    status='ok',
    error=None,
)
live = JupyterEvent(
    schema_id='trail/notebook-execution/v1',
    server='http://localhost:8888/',
    src_path='/project/analysis.ipynb',
    timestamp=datetime.fromisoformat(live_data['ended_at']),
    data=live_data,
)
live.entry = notebook_asset
```


```python
data = dict(
    verb='executed',
    source='live',
    kernel_id='kernel-1',
    kernel_session='kernel-process-1',
    execution_count=12,
    request_id='request-12',
    cell_id='cell-3',
    code="frame = pd.read_csv('roads.csv')",
    started_at='2026-11-03T18:00:00+00:00',
    ended_at='2026-11-03T18:00:01+00:00',
    status='ok',
    error=None,
)
schema_id='trail/notebook-execution/v1'
server='http://localhost:8888/'
src_path='/project/analysis.ipynb'
timestamp=datetime.fromisoformat(data['ended_at'])
live = JupyterEvent(schema_id=schema_id, server=server, src_path=src_path, timestamp=timestamp, data=data )

live.entry = notebook_asset
```


Here `timestamp` is the observed completion time. A failed execution could use `status='error'` and an error payload supplied by the callback or execution reply. The code string alone does not prove a filesystem read; that requires a separately observed operation.

For history recovered at 18:05, assume execution 11 came from the same running kernel, but its originating notebook was not recorded:

```python
recovered_data = dict(
    verb='recovered',
    source='history',
    kernel_id='kernel-1',
    kernel_session='kernel-process-1',
    execution_count=11,
    request_id=None,
    cell_id=None,
    code="frame = pd.read_csv('roads.csv')",
    started_at=None,
    ended_at=None,
    status=None,
    error=None,
)
recovered = JupyterEvent(
    schema_id='trail/notebook-execution/v1',
    server='http://localhost:8888/',
    timestamp=datetime.fromisoformat('2026-11-03T18:05:00+00:00'),
    data=recovered_data,
)
```

Here `entry` remains `None` and `src_path` remains empty. `timestamp` records recovery time, not execution time. The code and execution number are known; success, duration, notebook, and cell are not. A saved origin association could supply the notebook, but a current kernel-to-notebook association alone cannot establish historical origin.


## What history cannot establish

The SQLite input table stores session, execution number, transformed source, and raw source. It contains **no notebook path, cell ID, per-input timestamp, or success status**. Session timestamps do not supply individual execution times. Old sessions without a recorded notebook association cannot reliably be assigned retrospectively. [Database implementation](https://github.com/ipython/ipython/blob/main/IPython/core/history.py)

Persistent output logging is off by default. Failed inputs can appear in history, and execution requests can disable history recording. Code mentioning a file also does not prove which data was read or written. History can recover code and order; Trail's live events and file tracking remain necessary for timing, outcomes, and data provenance. [History settings](https://ipython.readthedocs.io/en/stable/api/generated/IPython.core.history.html), [execution protocol](https://jupyter-client.readthedocs.io/en/stable/messaging.html#execute)

Verified locally with IPython 9.17.1: `%history -n`, separation of two history sessions, database reload, and retention of an input that raised `ZeroDivisionError`.
