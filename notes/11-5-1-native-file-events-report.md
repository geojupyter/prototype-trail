# Native file events and process attribution for Trail

Trail needs to identify file activity, distinguish readers from writers, and find useful boundaries for folding many notifications into one recorded access. The [event-folding report](11-4-3-event-folding-report.md) starts from watchdog and Linux inotify. Watchdog's missing open and close events on macOS and Windows do not establish that the operating systems lack this information.

Linux inotify, exposed through watchdog, supplies open and close notifications without identifying the responsible process. Linux **fanotify** adds process identity with additional privileges. The corresponding sources to evaluate are **Endpoint Security on macOS** and **Event Tracing for Windows (ETW) on Windows**. A platform-specific collector could send these records to Trail's Python process. Windows also offers the **USN change journal**, which can report accumulated changes when a changed file closes, without identifying its writer.

The proposed default is ordinary-permission monitoring, with native collection as an optional capability and privilege limited to the collector where needed. Elevating all of Trail or Jupyter would impose a substantially greater deployment and trust burden.

This is a documentation-based assessment dated 2026-11-05. The collectors and integration described below are proposed. No macOS or Windows capture was run for this report.

## Available information

These interfaces differ in scope, correlation identifiers, and deployment requirements. They should feed a common recording layer without making their events appear more equivalent than they are.

| Source | Relevant information | Main deployment constraint |
|---|---|---|
| Linux inotify through watchdog | Opens, changes, writable and read-only closes; no writer PID | Ordinary filesystem permissions; no root required |
| Linux fanotify | File activity, closes, process identity | `CAP_SYS_ADMIN` to receive other processes' PIDs; Python wrapper documents root execution |
| macOS Endpoint Security, system-wide client | Opens, writes, closes, file changes, process identity | Apple-granted entitlement, signed/provisioned executable, root, Full Disk Access |
| macOS Endpoint Security, descendant client on macOS 27+ | Events from the collector and its descendant processes | Entitlement and signing/provisioning; no root or Full Disk Access; collector must be an ancestor of the observed processes |
| Windows ETW kernel file tracing | Opens, reads, writes, cleanup, file-object correlation, thread/process attribution | Elevated collector or appropriately privileged service for the proposed kernel tracing route |
| Windows USN standard reader | File identity, accumulated change reasons, close records; no writer PID | Administrator privileges for the standard volume reader |
| Windows USN unprivileged reader | Restricted journal reading without writer PID | No elevation for restricted reads; inaccessible filenames are withheld; suitable filesystem and existing journal required |

The sections below give the API contracts and sources behind this comparison.

> Are Windows and macOS missing equivalents of Linux's writable-close notifications, or can Trail obtain them outside watchdog?

> **Codex:** The operating systems expose richer native sources: Endpoint Security on macOS and kernel ETW on Windows. Watchdog's backend coverage is the limitation. Trail can collect native events separately and retain its Python folding and logging logic. USN provides a narrower Windows alternative without writer attribution.

> Is the main permissions difference that Linux watchdog works without elevation, whereas Endpoint Security and kernel ETW require it?

> **Codex:** For system-wide collection, broadly yes. Inotify supplies closes without root but lacks process identity; fanotify needs additional privilege for other processes' PIDs. Kernel ETW needs a privileged collector. System-wide Endpoint Security adds an Apple entitlement and Full Disk Access, but its descendant-only client avoids root and privacy approval while retaining the entitlement requirement. Restricted USN reading is another unprivileged exception.

## What a close establishes

A file close is a useful boundary, but it does not identify a complete application operation. A program can reopen a file, keep another writable open alive, or write several files during one save.

On Linux, `IN_CLOSE_WRITE` means that a file opened with write access was closed. It can occur without any data having been written. The earlier report's statement that it “marks the end of a write exactly” is therefore too strong. Its trace supports that interpretation for the observed GDAL operation, not for arbitrary writers. [Linux inotify manual](https://man7.org/linux/man-pages/man7/inotify.7.html)

> What is `IN_CLOSE_WRITE`?

> **Codex:** It is a Linux inotify event emitted when a writable open is closed, not a system call or proof that data changed. Another writer may remain open, or the program may reopen the file. The report therefore treats closure as evidence about an access boundary, not proof that a save operation finished.

Trail should distinguish three questions:

1. **Did data change?** Use evidence of successful writes or an explicit modification indication, with file metadata as supporting evidence.
2. **Did an observed open end?** Use the native close or cleanup semantics and any available correlation identifier.
3. **Did a save operation finish?** Infer this from the preceding evidence and subsequent activity, or obtain an explicit boundary from the writer.

## Linux: _watchdog and fanotify

Linux_ offers two distinct choices. Watchdog's inotify backend provides ordinary-permission notifications. Fanotify is a separate kernel interface that can also supply the responsible process.

### Watchdog's Linux-only open and close events

Watchdog uses a common API across platforms, but its backends do not supply identical events. Its inotify backend maps the following Linux events:

| inotify event | watchdog class | Handler method |
|---|---|---|
| `IN_OPEN` | `FileOpenedEvent` | `on_opened` |
| `IN_CLOSE_WRITE` | `FileClosedEvent` | `on_closed` |
| `IN_CLOSE_NOWRITE` | `FileClosedNoWriteEvent` | `on_closed_no_write` |

These events also reach `on_any_event`. Watchdog's Windows and macOS backends do not emit these classes; requesting them does not synthesize equivalents. Creation, modification, deletion, and movement are supported across those backends. [Watchdog event definitions](https://github.com/gorakhargosh/watchdog/blob/master/src/watchdog/events.py)

> Does watchdog's cross-platform API mean that `FileClosedEvent` also occurs on Windows and macOS?

> **Codex:** No. `FileClosedEvent`, `FileClosedNoWriteEvent`, and `FileOpenedEvent` are supplied by the Linux inotify backend. Cross-platform support means a shared API over different native sources, not equal event coverage on every platform.

Trail currently omits both close classes from `EVENT_FILTER` in [session.py](../src/trail/session.py). To receive writable closes, import `FileClosedEvent` and include it in the filter passed to `Observer.schedule`. Include `FileClosedNoWriteEvent` if read-only closes are needed. Omitting watchdog's event filter also enables its default close coverage on Linux. [Inotify translation and filter construction](https://github.com/gorakhargosh/watchdog/blob/master/src/watchdog/observers/inotify.py)

Trail must then handle closes as folding input before ordinary logging and directory-asset counting. Its `Access` counters use `OPS` from [event.py](../src/trail/event.py), which does not contain `closed` or `closed_no_write`. Enabling the filter alone would therefore leave the consumer unprepared for the new types.

> Do we already get `IN_CLOSE_WRITE` through watchdog, and how would Trail request it?

> **Codex:** Watchdog supports it on Linux, but Trail filters it out. Add `FileClosedEvent` to `EVENT_FILTER`; the resulting event has `event_type == "closed"` and reaches `on_closed` and `on_any_event`. Trail's existing `on_any_event` path can receive it, but its folding and directory-asset handling must be updated before enabling delivery. No watchdog modification or direct inotify binding is needed.

Inotify carries no user or process identity. Reading inotify directly instead of using watchdog would not recover a writer PID. [Inotify limitations](https://man7.org/linux/man-pages/man7/inotify.7.html)

### Fanotify and its Python binding

Fanotify supplies event metadata containing a process ID and supports `FAN_MODIFY`, `FAN_CLOSE_WRITE`, and other access events. Without `CAP_SYS_ADMIN`, a listener receives the originating PID only for events the listening process itself caused. Observing another process's PID therefore requires a privileged collector. As with other backends, a close identifies the closer; retain modification events when attributing writes. [Fanotify events](https://man7.org/linux/man-pages/man7/fanotify.7.html), [permission restrictions](https://man7.org/linux/man-pages/man2/fanotify_init.2.html)

The existing **pyfanotify** package exposes `FanotifyData.pid`, event flags in `ev_types`, and path/process details when available. It runs a collector process internally and handles communication through its Python API. Its documentation requires root execution; capability-only deployment needs validation against the wrapper. Google's older `python-fanotify` package is explicitly unmaintained and archived. [pyfanotify API](https://pyfanotify.readthedocs.io/en/latest/module.html), [package](https://github.com/baskiton/pyfanotify), [archived Google binding](https://github.com/google/python-fanotify)

> Are Python bindings available for fanotify, or would we have to wrap a command-line collector ourselves?

> **Codex:** `pyfanotify` already provides bindings and event PIDs. It manages its own collector process, so no custom native binding or command-output parser is required. A privileged Python helper could use it while Trail remains unprivileged. The package's root requirement and compatibility still need testing; the Google binding is archived.

## macOS: Endpoint Security

Endpoint Security is Apple's native interface for observing security-relevant operations. It supplies the file activity that FSEvents and watchdog's macOS backend do not expose as open and close events.

### File operations and process identity

A native client subscribes with `es_subscribe` to notification events including `ES_EVENT_TYPE_NOTIFY_OPEN`, `WRITE`, `CLOSE`, `CREATE`, `RENAME`, and `UNLINK`. The names after `OPEN` use the same `ES_EVENT_TYPE_NOTIFY_` prefix. A close contains the target file and a `modified` Boolean. The surrounding `es_message_t` supplies process information, including an audit token from which a PID can be obtained. [Event types](https://developer.apple.com/documentation/endpointsecurity/es_event_type_t), [close fields](https://developer.apple.com/documentation/endpointsecurity/es_event_close_t), [Apple's event inspection demonstration](https://developer.apple.com/videos/play/wwdc2022/110345/)

This would let Trail distinguish a Jupyter kernel's activity from an indexer's or an independently running application's activity. The process attached to a close identifies the closer; attribution of writes should retain the corresponding write events. An open establishes access mode, not that the application actually read data.

### Capture before implementing a client

Since macOS Ventura, Apple's built-in `eslogger` can emit Endpoint Security notification events as JSON. An initial capture would be:

```sh
sudo /usr/bin/eslogger open write close create rename unlink
```

The responsible terminal needs Full Disk Access. Use this to inspect event sequences and identify the fields Trail needs. Apple's manual explicitly treats its output as a diagnostic interface without a stable application API; a shipped collector should use Endpoint Security directly. [Apple introduction](https://developer.apple.com/videos/play/wwdc2022/110345/), [Apple's eslogger manual, mirrored](https://keith.github.io/xcode-man-pages/eslogger.1.html)

No maintained, ready-made Python Endpoint Security binding was identified in this review. PyObjC's module index does not list Endpoint Security; its separate `Security` binding is not an Endpoint Security client. Python could consume `eslogger` JSON Lines through a subprocess for experiments. For production, either write a binding or use a signed native helper that sends records through a pipe. A helper isolates the entitlement and native callback handling from Trail's Python environment. [PyObjC module index](https://pyobjc.readthedocs.io/en/latest/py-modindex.html)

> Does the lack of an identified Python binding mean macOS must use an `eslogger` subprocess?

> **Codex:** An `eslogger` subprocess is a convenient prototype, not the only route. Its JSON output is not a stable application API, and it still needs root and Full Disk Access. Production options are a custom binding or a signed native helper communicating with Python. The absence of a binding found in this review is not proof that none exists.

### Choose the observation scope

The system-wide entry point, `es_new_client`, requires the restricted `com.apple.developer.endpoint-security.client` entitlement, root privilege, and Full Disk Access approval. Apple must grant the entitlement, and the executable must be signed and provisioned appropriately. Adding the entitlement to a file alone is insufficient. [Client creation](https://developer.apple.com/documentation/endpointsecurity/es_new_client(_:_:)), [privilege errors](https://developer.apple.com/documentation/endpointsecurity/client), [entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.endpoint-security.client)

macOS 27 introduces `es_new_descendants_client`. It observes the calling process and its existing and future descendants. It still requires the entitlement, but does not require root or privacy approval through Transparency, Consent, and Control (TCC), including Full Disk Access. [Descendant client](https://developer.apple.com/documentation/endpointsecurity/es_new_descendants_client(_:_:)?changes=_4)

For Trail, this creates a concrete launch dependency:

- A collector that launches Jupyter can observe the server and kernels in that descendant tree.
- A helper started by an already running Jupyter server cannot observe its parent through this API.
- Independently launched applications, such as QGIS, remain outside the descendant scope.

The descendant client is the first native option to evaluate for an optional collector limited to Jupyter on macOS 27+. System-wide observation is needed when that collector must capture unrelated applications. Apple entitlement approval remains a deployment requirement in either case.

### Ordinary-permission alternatives do not report writable closes

For arbitrary external processes, no supported ordinary-user equivalent of `IN_CLOSE_WRITE` was identified. FSEvents reports filesystem changes. The vnode filter of `kqueue`, another native notification interface, reports `NOTE_WRITE`, extension, rename, deletion, and related changes, but has no writable-close event. Replacing watchdog with either interface would not supply a close boundary. [Apple's kqueue guide](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/FSEvents_ProgGuide/KernelQueues/KernelQueues.html), [current vnode flags](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/event.h)

Without elevation or an Endpoint Security entitlement, Trail can use a quiet-period heuristic or obtain explicit boundaries from an instrumented writer. Instrumentation covers participating writers, not arbitrary external applications.

> Is there a macOS equivalent that reports writable closes without administrator permissions?

> **Codex:** There is no general ordinary-user close-write watcher identified for arbitrary processes. FSEvents and kqueue report changes without closure. The scoped exception is Endpoint Security's macOS 27 descendant client: no root or Full Disk Access, but an Apple entitlement and the correct launch ancestry are still required. Otherwise, use a quiet-period heuristic or writer instrumentation.

## Windows: ETW kernel file tracing

ETW is Windows' event tracing infrastructure. Its kernel file events describe file operations and provide identifiers for following the lifetime of an opened file object. This is the first Windows source to evaluate when Trail needs both access boundaries and attribution. A Python collector can use **pywintrace**, a wrapper for controlling ETW sessions and consuming event records.

### Opens, I/O, and cleanup

The relevant events have different meanings from ordinary directory-change notifications:

| ETW event | Meaning and use |
|---|---|
| `FileIo/Create` | Open or create operation; includes the path and a `FileObject` identifier |
| `FileIo/Read`, `FileIo/Write` | I/O operation; includes the file object, offset, requested byte count, and issuing thread identifier |
| `FileIo/Cleanup` | Last handle to the corresponding file object has closed |
| `FileIo/Close` | File object is being freed |

Use `Cleanup` for the handle-close boundary. `Close` describes object lifetime and may occur later. Separate opens of one path can have separate file objects, so one cleanup does not establish that all writers to that path have finished. Microsoft's driver documentation defines cleanup in terms of the final handle to a file object. [Open fields](https://learn.microsoft.com/en-us/windows/win32/etw/fileio-create), [read/write fields](https://learn.microsoft.com/en-us/windows/win32/etw/fileio-readwrite), [cleanup and close](https://learn.microsoft.com/en-us/windows/win32/etw/fileio-simpleop), [file-object cleanup semantics](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/dispatchcleanup-routines)

The collector must correlate operations by `FileObject` within its lifetime, resolve filenames, and associate issuing threads with processes. Classic `FileIo/Write` supplies the issuing thread as `TTID`; thread lifecycle and rundown events associate that thread with its process. Do not blindly treat the event header's `ProcessId` as the original writer. A write event's byte count is the requested size; it is not by itself proof that all requested bytes were successfully written. [Thread/process fields](https://learn.microsoft.com/en-us/windows/win32/etw/thread-v2-typegroup1)

```text
FileIo/Write.TTID → thread-to-process mapping → writer PID
```

> Endpoint Security identifies the process performing a write. Can watchdog on Linux or pywintrace on Windows do the same?

> **Codex:** Linux watchdog cannot: inotify has no process identity, and a different inotify binding would not change that. Windows kernel ETW exposes the information needed for attribution. For classic file-write events, correlate `TTID` with thread/process events; pywintrace exposes records but does not implement that correlation for Trail. On Linux, fanotify supplies process identity with the privilege requirements described above.

### Access from Trail

**pywintrace** provides a Python `ctypes` wrapper for controlling ETW sessions and consuming decoded events through callbacks. Its implementation handles kernel sessions, exposes event headers, and defines the file-I/O flags. It is a candidate for a Python collector without a custom native binding or C# process. Verify the required open, write, cleanup, and thread fields on the supported Windows/Python versions before adopting it. **PyETWkit** is another candidate with a Rust-backed Python API and kernel file-I/O support; no capture or performance validation was performed here. [pywintrace](https://github.com/fireeye/pywintrace), [kernel-session implementation](https://github.com/fireeye/pywintrace/blob/master/etw/etw.py), [PyETWkit](https://github.com/m96-chan/PyETWkit), [file-I/O categories](https://github.com/m96-chan/PyETWkit/blob/main/crates/pyetwkit-core/src/kernel.rs)

Do not confuse these consumers with **pyetw**, which writes Python logging records into ETW, or **python-etwtrace**, which instruments Python for profiling. Neither is the general kernel file-event consumer needed here. [pyetw](https://pyetw.readthedocs.io/en/latest/introduction.html), [python-etwtrace](https://github.com/microsoft/python-etwtrace)

Microsoft's **TraceEvent** library remains an alternative C# implementation route. Either collector would start a real-time kernel file-I/O session, collect the process/thread events needed for attribution, maintain correlation state, and send records for tracked paths to Trail. Neither route requires writing a filesystem driver. [TraceEvent programming guide](https://github.com/microsoft/perfview/blob/main/documentation/TraceEvent/TraceEventProgrammersGuide.md)

> Are Python libraries available for macOS Endpoint Security and Windows kernel ETW tracing?

> **Codex:** Windows has pywintrace and PyETWkit, so a C# helper is optional. Their precise decoding and capture behavior still need validation for Trail. No maintained ready-made Python Endpoint Security binding was identified; use eslogger for macOS experiments and a native helper or custom binding for production. Bindings do not remove the operating system's permission requirements.

For the proposed kernel tracing route, use an elevated helper or appropriately privileged service. Jupyter itself can continue under the normal user's account. Windows distinguishes ordinary trace-session permissions from the stricter permissions of the NT Kernel Logger; membership in Performance Log Users is not a universal substitute for elevation. [StartTrace permissions](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/nf-evntrace-starttracea)

> Must the Python process using pywintrace run elevated?

> **Codex:** Yes, for the proposed use of starting and controlling a kernel ETW session. Elevate only the Python collector or run it as a suitably privileged service, then send records to ordinary-permission Trail. A separately running Jupyter server and its kernels do not need elevation.

ETW buffers delivery, so receipt time can lag occurrence time. Preserve the event timestamp separately from the time Trail receives the record. Delivery latency and tracing overhead need measurement before choosing folding deadlines.

## Windows alternative: USN change journal

The USN journal records accumulated filesystem changes. It is a narrower alternative when Trail needs evidence of modification followed by closure, but does not need read history or the responsible process.

The standard volume-reader workflow uses `DeviceIoControl`:

1. Open the volume and query its journal with `FSCTL_QUERY_USN_JOURNAL`.
2. Read subsequent records with `FSCTL_READ_USN_JOURNAL`.
3. Include `USN_REASON_CLOSE` in `ReasonMask` and set `ReturnOnlyOnClose = 1`.
4. Inspect the returned reasons for data overwrite, extension, or truncation, then resolve file identities to tracked paths.

Microsoft documents this setting as returning changes when the final handle of the changed file or directory closes. A close record carries accumulated change reasons, which is useful for folding. [Read options](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-read_usn_journal_data_v0)

USN records include a file reference, parent reference, filename, timestamp, and reasons. They contain no writer PID and cannot reconstruct read-only access sessions. Journal availability, rollover, filesystem support, and path resolution are additional concerns. [Record layout](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-usn_record_v2)

Microsoft documents administrator privileges for the standard volume-reading route above. There is also an implemented restricted route using `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL`. Google's NtObjectManager opens a root-directory handle with reduced access for its unprivileged reader; its documentation states that inaccessible filenames are withheld. This is more than an SDK constant: the published reader selects that control code, queries the journal, and parses returned records. [Standard reader privileges](https://learn.microsoft.com/en-us/windows/win32/fileio/using-the-change-journal-identifier), [unprivileged handle setup and filename restriction](https://github.com/googleprojectzero/sandbox-attacksurface-analysis-tools/blob/main/NtObjectManager/NtFileFunctions.ps1#L1103), [reader implementation](https://github.com/googleprojectzero/sandbox-attacksurface-analysis-tools/blob/main/NtCoreLib/IO/UsnJournal/UsnJournalUtils.cs)

Restricted USN reading is therefore a candidate for ordinary-permission modification and close detection. Trail still needs to validate close filtering, delivery latency, path reconstruction, journal access, and gap recovery on its supported Windows/filesystem versions. It cannot recover writer PIDs or read-only access sessions from either USN reader.

> Can the USN change journal be read without administrator permissions, unlike kernel ETW?

> **Codex:** Yes, through the separate restricted reader. The standard volume reader requires administrator privileges; `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL` supports unprivileged reads and withholds inaccessible filenames. Existing code demonstrates that route. It remains a change/close source without writer attribution, and its suitability for Trail needs testing. The report now distinguishes those two readers explicitly.

## Deployment and adoption

Requiring all of Trail to run elevated is technically possible, but the expected adoption cost is substantial. The distinction is between a user approving installation of a collector and needing administrator access for every ordinary Trail session. The assessment below is a deployment judgment, not a measured user survey.

| Environment | Expected effect of mandatory elevation |
|---|---|
| Personally owned computer with administrator access | Feasible, with additional installation and consent steps |
| Managed university/company computer without administrator access | Blocks use until IT approves deployment |
| Shared JupyterHub or research server | Requires operator installation and integration |
| Workshops, temporary environments, disposable virtual environments | Makes setup considerably harder than installing a Python package |

On Windows, a standard user's own credentials do not authorize elevation; an administrator must provide credentials or deploy the collector. An installed service can keep collection separate from subsequent unprivileged sessions. Microsoft recommends isolating privileged operations in a helper rather than elevating the whole application. [UAC behavior](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works), [helper-process guidance](https://learn.microsoft.com/en-us/windows/win32/secbp/running-with-administrator-privileges)

On macOS, system-wide Endpoint Security also requires Apple entitlement approval, signing/provisioning, and Full Disk Access. Running `sudo trail` does not satisfy those additional requirements. The descendant client reduces the user permission burden, but introduces an entitlement and launch-ancestry requirement.

A separate elevated observer does not elevate an already running Jupyter server. Elevating Jupyter itself, however, gives notebook code and its dependencies those privileges. Jupyter's security model treats server access as access to arbitrary code execution. [Jupyter security model](https://jupyter-server.readthedocs.io/en/latest/operators/security.html)

A production collector also needs protected installation files, authenticated communication, and per-user restrictions on delivered events. Privileged Python must not import code from an unprivileged user's writable project or virtual environment. These are implementation requirements of the proposed privilege boundary.

> Would requiring administrator execution be feasible for Trail, or would it prevent many users from adopting it?

> **Codex:** Mandatory elevation would likely block users on managed institutional machines and require operator involvement on shared services. It is manageable for research prototypes and personal machines, but should not be required to start using Trail. Keep ordinary-permission monitoring as the default, make richer privileged collection optional, and keep notebook execution unprivileged. The report now treats this as the deployment recommendation rather than assuming native collection is mandatory.

## Proposed integration with Trail

Trail's observer wraps each watchdog event and its receipt time in an `Arrival` record. `Access` holds event counts and timing for one directory-asset access; `Accesses` manages those open accesses. All three live in [session.py](../src/trail/session.py). A native collector would supply richer input to the same recording problem. Folding policy should be independent of the library that collected an event.

The proposed deployment has three capabilities:

1. **Default monitoring:** ordinary filesystem permissions, with close events where available and inferred access boundaries elsewhere. Writer identity remains unknown when the source cannot supply it.
2. **Optional native collection:** fanotify, Endpoint Security, or ETW supplies additional closure and attribution evidence under the corresponding permissions. Only the collector needs the backend-specific privileges or entitlement.
3. **Writer integration:** explicit application-operation boundaries supplement filesystem observation for participating Jupyter workflows. This does not capture arbitrary external writers.

```text
Ordinary-permission watcher ─┐
                            ├─ normalized records → access folding → Trail log
Optional native collector ──┘
```

Each normalized record should preserve the operation, occurrence and receipt times, file identity and paths, process identity when available, and the native source. Keep `FileObject` correlation and explicit modification indications as optional source-specific fields. Do not invent a matching open identifier for a backend that does not supply one.

Use close or cleanup as evidence for ending an observed access, then apply any settling rule at the recording layer. If an indexer's read belongs to a different process, it need not extend the kernel's write session. Keep a separate rule for long-lived writable opens and for sources without closure information. Settling periods from the Linux trace are starting hypotheses, not measurements for these collectors.

Collection can lose events. Endpoint Security exposes sequence numbers for detecting gaps; ETW exposes lost-event statistics. A gap should invalidate affected correlation assumptions and trigger reconciliation, rather than silently producing a supposedly complete access history. [Endpoint Security sequence numbers](https://developer.apple.com/documentation/endpointsecurity/es_message_t/seq_num), [ETW session statistics](https://learn.microsoft.com/en-us/windows/win32/etw/event-trace-properties)

Even a complete stream does not identify one `GeoDataFrame.to_file` call automatically. A save can span several files, opens, and transactions. If exact application-operation boundaries are required, writer instrumentation must supplement filesystem observation.

## Evaluation needed before implementation

The next work is to validate the optional native collectors and restricted USN reader while retaining a usable ordinary-permission mode. No backend or deployment choice below has been implemented or tested for this report.

1. Capture the existing GeoPackage example with `pyfanotify` on Linux, `eslogger` on macOS, and a minimal `pywintrace` collector on Windows. Compare opens, writes, closure, process identity, and delivery delay. Keep TraceEvent as an alternative Windows implementation.
2. Repeat with an unchanged writable open, overlapping writers, duplicated handles, rapid reopen, and a dataset kept open across notebook cells.
3. Check temporary-file replacement, sidecar files, process termination, and memory-mapped writes. In particular, test closing a descriptor while a writable mapping remains alive.
4. Establish how unsuccessful and asynchronous I/O appears before interpreting events as completed writes.
5. Measure collection overhead and event loss during heavy directory-asset writes. Test recovery after collector restart or a detected gap.
6. Prove deployment: a privileged collector on Linux and Windows, entitlement and signing on macOS, and Jupyter launch ancestry for the descendant client. Confirm that Trail and notebook execution remain unprivileged.
7. Evaluate restricted USN reading separately when attribution is unnecessary, including inaccessible or deleted files and journal rollover. Confirm that ordinary-permission Trail remains useful when no native collector is available.

The evidence needed to proceed is a repeatable association between file changes, their observed access boundaries, and the relevant Jupyter process, with documented behavior when that association is incomplete.
