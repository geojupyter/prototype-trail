# Setup

Use Bash or Zsh on macOS/Linux, or PowerShell on Windows. Commands are the same unless separate blocks are shown.

Run every command in the folder you keep projects in. The setup puts three folders there:

```
projects/
├── trail/            Trail
├── eventfeed/        JupyterGIS with the event feed
└── trail-tutorial/   the copy of the tutorial project
```

## Get Micromamba

The tutorials use [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) to manage the environment. Any conda-compatible manager works with the same packages. Follow the installation and shell initialization instructions for your operating system, reopen the terminal, then create the environment:

```bash
micromamba create -y -n trail -c conda-forge pip "python=3.13.*" "nodejs=24" pnpm qgis
```

## Activate Micromamba

Run this in every new terminal:

```bash
micromamba activate trail
```

## Get Trail

```bash
git clone -b stable https://github.com/geojupyter/prototype-trail.git trail
python -m pip install -e ./trail
```

Copy the sample assets, then add the guides and notebooks:

**macOS / Linux**

```bash
cp -r trail/tutorials/project trail-tutorial
cp trail/tutorials/*.md trail/tutorials/*.ipynb trail-tutorial/
```

**Windows (PowerShell)**

```powershell
Copy-Item -Recurse trail/tutorials/project trail-tutorial
Copy-Item -Path trail/tutorials/*.md, trail/tutorials/*.ipynb -Destination trail-tutorial
```

## Get JupyterGIS

JupyterGIS with the event feed is not released, so this builds it from source:

```bash
git clone -b stable https://github.com/hodczak/prototype-jupytergis-eventfeed.git eventfeed
cd eventfeed
python scripts/dev-install.py
cd ..
```

## Start

Open two terminals and activate the environment in each.

Terminal 1:

```bash
cd trail-tutorial
jupyter lab
```

Terminal 2:

```bash
cd trail-tutorial
trail
```

The header's `jupyter 1` indicates one active Jupyter event subscription. Open the copied notebooks in JupyterLab.

## Start over

Stop Trail and JupyterLab, delete `trail-tutorial`, then copy it again:

**macOS / Linux**

```bash
cp -r trail/tutorials/project trail-tutorial
cp trail/tutorials/*.md trail/tutorials/*.ipynb trail-tutorial/
```

**Windows (PowerShell)**

```powershell
Copy-Item -Recurse trail/tutorials/project trail-tutorial
Copy-Item -Path trail/tutorials/*.md, trail/tutorials/*.ipynb -Destination trail-tutorial
```
