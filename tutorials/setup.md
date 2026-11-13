# Setup

The tutorials need three things:

- JupyterLab with JupyterGIS, the map editor, built with the event feed that reports map edits to Jupyter Server
- Trail, which records those reports and the project's file activity
- a copy of the tutorial project to work in

This page installs JupyterGIS and Trail into one environment, `trail`. Keep using it for your own projects after the tutorials. JupyterGIS with the event feed is not released, so the install builds it from source.

Everything goes in the folder that holds your Trail checkout, wherever that is, such as `~/projects`, `~/Downloads` or your home folder:

```
projects/
├── trail/            Trail
├── eventfeed/        JupyterGIS with the event feed
└── trail-tutorial/   the copy of the tutorial project
```

## Requirements

- git
- [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html), set up for your shell

## Get Trail

Skip this if you already have a Trail checkout. Otherwise, open a terminal in the folder you keep projects in and run:

```bash
git clone -b hodczak-prototype https://github.com/geojupyter/prototype-trail.git trail
cd trail
```

## Install

Run once, in a terminal in your Trail checkout. This clones JupyterGIS next to it, creates the environment, installs Trail, copies the project next to it and builds JupyterGIS. The build runs last, so if it hangs, it is the only step left to repeat.

**Linux and macOS**

```bash
git clone -b hodczak-prototype https://github.com/hodczak/prototype-jupytergis-eventfeed.git ../eventfeed
micromamba create -y -n trail -c conda-forge pip "python=3.13.*" "nodejs=24" pnpm qgis
micromamba activate trail
pip install -e .
cp -r tutorials/project ../trail-tutorial
cd ../eventfeed
python scripts/dev-install.py
```

**Windows (PowerShell)**

```powershell
git clone -b hodczak-prototype https://github.com/hodczak/prototype-jupytergis-eventfeed.git ../eventfeed
micromamba create -y -n trail -c conda-forge pip "python=3.13.*" "nodejs=24" pnpm qgis
micromamba activate trail
pip install -e .
Copy-Item -Recurse tutorials/project ../trail-tutorial
cd ../eventfeed
python scripts/dev-install.py
```

If the build stops making progress, see [The build hangs](#the-build-hangs).

## Start

Run each time, in two terminals, each opened in your Trail checkout. These commands are the same in bash, zsh and PowerShell.

Terminal 1 starts JupyterLab and opens it in your browser:

```bash
micromamba activate trail
cd ../trail-tutorial
jupyter lab
```

The file browser should list `data` and `maps`.

Terminal 2 starts Trail:

```bash
micromamba activate trail
cd ../trail-tutorial
trail
```

Wait for `jupyter 1/1` in Trail's header. Trail has found JupyterLab and is listening.

## Stop

Enter `quit` in Trail. Press Ctrl+C twice in the JupyterLab terminal.

## Start over

The tutorials change the project's files and Trail's log. To start again from the original project, stop Trail and JupyterLab, then replace the copy from a terminal in your Trail checkout.

**Linux and macOS**

```bash
rm -rf ../trail-tutorial
cp -r tutorials/project ../trail-tutorial
```

**Windows (PowerShell)**

```powershell
Remove-Item -Recurse -Force ../trail-tutorial
Copy-Item -Recurse tutorials/project ../trail-tutorial
```

## Other systems

Trail is developed on Linux. Its records of file activity come from Linux file notifications, so they can differ on macOS and Windows.

## Troubleshooting

### The build hangs

This applies only if both are true:

- the install stopped making progress at `python scripts/dev-install.py`
- your home folder has a `.gitignore` file

An Nx bug makes the JupyterGIS build hang on that file, as JupyterGIS's troubleshooting guide describes. Press Ctrl+C. The terminal is still in `eventfeed`, so build again there with the file renamed.

**Linux and macOS**

```bash
mv ~/.gitignore ~/.gitignore.off
python scripts/dev-install.py
mv ~/.gitignore.off ~/.gitignore
```

**Windows (PowerShell)**

```powershell
Rename-Item $HOME/.gitignore .gitignore.off
python scripts/dev-install.py
Rename-Item $HOME/.gitignore.off .gitignore
```
