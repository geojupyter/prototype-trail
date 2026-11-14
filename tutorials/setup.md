# Setup

Run every command in the folder you keep projects in. The setup puts three folders there:

```
projects/
├── trail/            Trail
├── eventfeed/        JupyterGIS with the event feed
└── trail-tutorial/   the copy of the tutorial project
```

## Get Micromamba

The tutorials use [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) to manage the environment. Any conda-compatible manager works with the same packages. Install it, then create the environment:

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
git clone -b hodczak-prototype https://github.com/geojupyter/prototype-trail.git trail
pip install -e ./trail
cp -r trail/tutorials/project trail-tutorial
```

## Get JupyterGIS

JupyterGIS with the event feed is not released, so this builds it from source:

```bash
git clone -b hodczak-prototype https://github.com/hodczak/prototype-jupytergis-eventfeed.git eventfeed
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

Trail is ready when its header shows `jupyter 1/1`.

## Stop

Enter `quit` in Trail. Press Ctrl+C twice in the JupyterLab terminal.

## Start over

Stop Trail and JupyterLab, delete `trail-tutorial`, then copy it again:

```bash
cp -r trail/tutorials/project trail-tutorial
```
