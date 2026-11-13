# Event coverage summary

Each new event in two lines: what happened, then the record with only the fields that matter. Fields and values are as the console prints them, from the runs of 12 November 2026. [11-12-2-event-coverage-report.md](11-12-2-event-coverage-report.md) has the full records.

## Notebooks

**Cell Started**\
`ExecutionEvent(verb='started', src_path='Untitled6.ipynb', execution_count=3, code='1 / 0')`

**Cell Completed**\
`ExecutionEvent(verb='completed', src_path='Untitled6.ipynb', execution_count=2, duration='0.013 s', outputs='stream 1, execute_result 1')`

**Cell Errored**\
`ExecutionEvent(verb='errored', src_path='Untitled6.ipynb', execution_count=3, error='ZeroDivisionError: division by zero')`

**Cell Recovered**\
`ExecutionEvent(verb='recovered', execution_count=1, code='from jupytergis import GISDocument (+2 lines)', paths=['Untitled1.ipynb'])`

**Kernel Attached**\
`KernelSessionEvent(verb='attached', src_path='Untitled6.ipynb', kernel_name='python3')`

**Kernel Restarted**\
`KernelSessionEvent(verb='restarted', src_path='Untitled6.ipynb', kernel_name='python3')`

**Kernel Detached**\
`KernelSessionEvent(verb='detached', src_path='Untitled6.ipynb', kernel_name='python3')`

**Notebook Saved**\
`ContentsServiceV1Event(verb='saved', src_path='Untitled6.ipynb', saves=7, writes=7)`

## Files

**File Created**\
`FileAccessEvent(event_type='created', src_path='data/counts.csv')`

**File Modified**\
`FileAccessEvent(event_type='modified', src_path='data/counts.csv', change=["columns: ['name', 'count'] → ['name', 'count', 'total']"])`

**File Opened**\
`FileAccessEvent(event_type='opened', src_path='data/counts.csv')`

**File Moved**\
`WatchdogEvent(event_type='moved', src_path='data/counts.csv', dest_path='data/totals.csv')`

**File Deleted**\
`WatchdogEvent(event_type='deleted', src_path='data/totals.csv')`

**GeoPackage Changed**\
`FileAccessEvent(event_type='modified', src_path='data/crossings.gpkg', change=["layer 'crossings' CRS EPSG:4326 → EPSG:3857", ..., "column 'lanes' added in layer 'crossings'"])`

**Raster Changed**\
`FileAccessEvent(event_type='created', src_path='data/scene.tif', change=['bands 3 → 4', 'CRS EPSG:32633 → EPSG:32634', 'nodata 0 → 255'])`

**Zarr Store Written**\
`DirectoryAccessEvent(event_type='modified', src_path='data/cube.zarr', count='opened 2, created 2, modified 7')`

**Zarr Store Changed**\
`MetadataEvent(basis='watchdog', src_path='data/cube.zarr', format='zarr', change=["array 'temperature' shape [4, 32, 32] → [3, 32, 32]", ..., "array 'pressure' added"])`

**Map File Changed**\
`MetadataEvent(basis='watchdog', src_path='maps/city.jGIS', format='jgis', change=["layer 'Marker' added", "source 'Marker' added", 'layer order changed'])`

**File Changed While Off**\
`MetadataEvent(basis='startup', src_path='data/crossings.gpkg', format='geopackage', change=["column 'checked' added in layer 'crossings'"])`

## Maps

**Map Created**\
`DocumentV1Event(verb='created', src_path='untitled.jGIS')`

**Map Opened**\
`DocumentV1Event(verb='opened', src_path='maps/city.jGIS')`

**Map Closed**\
`DocumentV1Event(verb='closed', src_path='maps/city.jGIS')`

**Map Focused**\
`DocumentV1Event(verb='activated', src_path='maps/city.jGIS')`

**Map Unfocused**\
`DocumentV1Event(verb='deactivated', src_path='maps/city.jGIS')`

**View Moved**\
`ViewportV1Event(verb='moved', src_path='maps/city.jGIS', coordinates={'x': -13611034.139293559, 'y': 4561086.969892361}, zoom=13)`

**Tool Changed**\
`ModeV1Event(verb='changed', src_path='maps/city.jGIS', mode='Add Marker', previous='none')`

**Layer Added**\
`LayerV1Event(verb='added', src_path='maps/city.jGIS', layer='crossings', resource_path='data/crossings.geojson')`

**Layer Updated**\
`LayerV1Event(verb='updated', src_path='maps/city.jGIS', layer='streets', change=['visible False → True', 'opacity 0.7 → 0.3'])`

**Layer Removed**\
`LayerV1Event(verb='removed', src_path='maps/city.jGIS', layer='crossings', resource_path='data/crossings_2026.geojson')`

**Layer Added from Python**\
`LayerV1Event(verb='added', src_path='maps/city.jGIS', local=False, layer='from python')`

**Filter Added**\
`LayerV1Event(verb='updated', src_path='maps/earthquakes.jGIS', layer='Earthquakes', filter_added='mag > 5')`

**Source Added**\
`SourceV1Event(verb='added', src_path='maps/city.jGIS', resource_path='data/crossings.geojson')`

**Source Replaced**\
`SourceV1Event(verb='updated', src_path='maps/city.jGIS', resource_path='data/crossings_2026.geojson', previous_resource_path='data/crossings.geojson')`

**Source Removed**\
`SourceV1Event(verb='removed', src_path='maps/city.jGIS', resource_path='data/crossings_2026.geojson')`

## Project

**Trail Initialized**\
`InitTrailEvent(ontrail=['./', '*.jgis', '*.qgz', ..., '*.ipynb', '*.csv'], offtrail=['.*'])`
