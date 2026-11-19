# Image and yaml files in the `.ontrail` preset

The bundled preset is `src/trail/ontrail.txt`. It lists the `*.ext` rules a new `.ontrail` starts with. This note records whether image and yaml files belong in it.

## Meeting notes and whether images are assets

> Check PROJECT_PLAN.md, the shared DSE GeoJupyter notes and the last three months of compass. Compass may be missing the November meeting notes. Should images such as png be tracked as assets and show up in `.ontrail`?

**Claude:** Compass is current. The clone and `origin/main` are both at the 2026-10-20 notes. The only newer item is open PR #107, the 2026-11-10 community meeting, which is an empty template. The Nov 12 Trail meeting exists only in the shared doc. Nothing in `compass/` was changed.

My first answer was no: keep images out of the preset and leave them as a scoped opt-in such as `data/*.png`. The reasons were that the preset lists data formats and not renderings, that the team wants a user-curated list of tracked files, that a blanket glob would be noisy, and that Trail cannot yet say which cell wrote a file. Most of that did not survive the next comment.

## Images are data, and a rendered file is worth recording

> The noise count came from the code repo, which Trail does not track. The trail project example sits in a nested directory and its scope stays there. Leave images in `.ontrail`. Png files are used for training. Even when a script only renders an image, we likely want to track that.

**Claude:** Agreed on both points. The noise figure was measured on the wrong tree, and a png can be training data as much as a tif can. A script rendering a file is the event worth keeping, even before Trail can link it to a cell, because the overlapping run time already correlates the two. I added the image formats to the preset.

The change made `tests/test_ontrail.py` pass, and the one `test_init.py` failure in the same run (the example text "Check the data folder (relative)") was already there at `HEAD`.

## Which image extensions

> The list is too broad. Gif is unlikely to be an asset. Mostly png is needed for training runs. Check what else should be included or excluded for training.

**Claude:** The preset now adds only `png`, `jpg` and `jpeg`. Those cover nearly all training images: classification sets are mostly jpeg, segmentation masks and lossless sets are png, and matplotlib writes png by default. `bmp`, `gif` and `webp` were removed as rare in training sets. `svg` was removed because it is a vector render and not training data. It can be added per project.

The preset already covers the other training formats: `npy`, `npz`, `h5`, `tfrecord`, `pt`, `pth`, `ckpt`, `safetensors`, `onnx`, `keras`, `joblib`, `pkl`, `jsonl`, `parquet` and `tif`. Not added, because each is niche or too generic: `tflite`, `gguf`, `mdb` and `tar`.

## yaml and yml

> Yaml and yml should probably be included, because loading metadata is an important event to track in a research workflow. Does the project proposal support logging which yaml metadata was loaded?

**Claude:** Yes, and `*.yaml` and `*.yml` are now in the preset. The proposal does not name yaml. `scratch/trail-initiative.md` supports it in general terms, since the project record holds resources with stable ids and events with linked resources. A config that a run loads is that kind of resource. A tracked yaml file records created, modified, moved and opened events, and the opened event shows which config a run used.

There are limits. Trail records that the file was opened or changed, not its values, because no metadata reader exists for yaml. The link from a cell to the files it touched is still unbuilt (`scratch/11-12-2-event-coverage.md:387`), so an opened event matches a run only by time. `*.yml` also matches `environment.yml` and `myst.yml`, which is probably acceptable. `toml`, `ini` and `cfg` are the same kind of file and were not added.
