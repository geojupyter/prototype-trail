# Bug reproductions

Each notebook here reproduces one known bug in Trail or the JupyterGIS event feed. It states the expected and observed records, gives the steps, and shows records from a run that reproduced it.

| Bug | Notebook |
|---|---|
| One map edit is recorded once for every copy of the map in the browser. | [duplicate-map-records.ipynb](duplicate-map-records.ipynb) |

## Setup

The bugs use the tutorials' environment. Follow [tutorial setup](../tutorials/setup.md) through **Get JupyterGIS**.

Then, in the folder you keep projects in, copy the bug project:

**macOS / Linux**

```bash
cp -r trail/bugs/project trail-bugs
cp trail/bugs/*.md trail/bugs/*.ipynb trail-bugs/
```

**Windows (PowerShell)**

```powershell
Copy-Item -Recurse trail/bugs/project trail-bugs
Copy-Item -Path trail/bugs/*.md, trail/bugs/*.ipynb -Destination trail-bugs
```

## Start

Open two terminals and activate the environment in each.

Terminal 1:

```bash
cd trail-bugs
jupyter lab
```

Terminal 2:

```bash
cd trail-bugs
trail
```

Wait for `jupyter 1` in Trail's header, then open a bug's notebook in JupyterLab.

## Start over

Each notebook assumes a fresh project. Stop Trail and JupyterLab, delete `trail-bugs`, then copy it again:

**macOS / Linux**

```bash
cp -r trail/bugs/project trail-bugs
cp trail/bugs/*.md trail/bugs/*.ipynb trail-bugs/
```

**Windows (PowerShell)**

```powershell
Copy-Item -Recurse trail/bugs/project trail-bugs
Copy-Item -Path trail/bugs/*.md, trail/bugs/*.ipynb -Destination trail-bugs
```
