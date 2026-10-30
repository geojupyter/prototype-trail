# Allow jupyter-collaboration 5 and jupyter-ydoc 4

Raises the jupyter-collaboration pins, so this branch can run with jupyter-collaboration 5, the latest server code for collaborative rooms.

- `jupyter-collaboration` to `<6`, and `jupyter-docprovider`, `jupyter-server-ydoc` and `jupyter-collaboration-ui` to `<4`. Upstream JupyterGIS made this change on 2026-09-11, in [geojupyter/jupytergis#1847](https://github.com/geojupyter/jupytergis/pull/1847).
- `jupyter-ydoc` to `<5` in `jupytergis_core`, `jupytergis_lab` and `jupytergis_qgis`. `jupyter-server-ydoc` 3 requires `jupyter-ydoc>=4.1.1`, so pip cannot install jupyter-collaboration 5 without this. Upstream main still pins `jupyter-ydoc<4`.

The first change should really come from merging upstream main, which this branch is 97 commits behind. The second would need its own upstream PR.

Tested with jupyter-collaboration 5.0.4, jupyter-ydoc 4.2.0 and pycrdt 0.14.10. A headless browser opened `examples/world.jGIS` and hid a layer. The map drew, the room saved the file, and the feed carried the same messages as with jupyter-collaboration 4.4.2.


<!-- readthedocs-preview jupytergis start -->
---
📚 Documentation preview: https://jupytergis--5.org.readthedocs.build/en/5/
💡 JupyterLite preview: https://jupytergis--5.org.readthedocs.build/en/5/lite
💡 Specta preview: https://jupytergis--5.org.readthedocs.build/en/5/lite/specta

<!-- readthedocs-preview jupytergis end -->
