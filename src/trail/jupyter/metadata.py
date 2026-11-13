from __future__ import annotations

import io
import json
import re
import zipfile
from typing import ClassVar
from urllib.parse import unquote

from trail.metadata import Metadata, Summary

SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|access_token|apikey|api_key|key|secret|authcfg"
    r"|sig|signature|user|username)=('(?:[^']|'')*'|\"[^\"]*\"|[^&\s]*)"
)
USERINFO = re.compile(r'(?<=://)[^/@\s]+@')


def sanitize(source: str) -> str:
    # a secret may hide behind percent-encoding
    out = unquote(source)
    out = SECRET.sub(r'\1=***', out)
    out = USERINFO.sub('***@', out)
    return out


class QGISMetadata(Metadata):
    name: ClassVar[str] = 'qgis'
    suffixes: ClassVar[tuple[str, ...]] = ('.qgs', '.qgz')
    max_bytes: ClassVar[int | None] = 64 * 2**20
    # the most the project XML inside a .qgz may expand to
    max_xml: ClassVar[int] = 256 * 2**20
    named: ClassVar[tuple[str, ...]] = ('layers',)
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('crs',): 'project CRS {old} → {new}',
        ('layers', '*'): "layer '{label}' {verb}",
        ('layers', '*', 'name'): "layer renamed '{old}' → '{new}'",
        ('layers', '*', 'source'): "layer '{label}' source changed",
        ('layers', '*', 'crs'): "layer '{label}' CRS {old} → {new}",
        ('layers', '*', 'is_visible'): "layer '{label}' visible {old} → {new}",
        ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
        ('order',): 'layer order changed',
    }

    def xml(self) -> bytes:
        content = self._asset.path.read_bytes()
        if not zipfile.is_zipfile(io.BytesIO(content)):
            return content
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [
                info
                for info in archive.infolist()
                if (
                    info.filename
                    .lower()
                    .endswith('.qgs')
                )
            ]
            if len(members) != 1:
                raise ValueError(f'Expected one .qgs member, found {len(members)}')
            member = members[0]
            if member.file_size > self.max_xml:
                raise ValueError(f'{member.filename} expands to {member.file_size} bytes')
            out = archive.read(member)
        return out

    def read(self) -> Summary:
        from defusedxml import ElementTree

        root = ElementTree.fromstring(self.xml())
        if root.tag != 'qgis':
            raise ValueError(f'Not a QGIS project: <{root.tag}>')
        tree = {
            node.get('id'): node
            for node in root.iter('layer-tree-layer')
        }
        layers = {}
        for layer in root.iterfind('projectlayers/maplayer'):
            identifier = layer.findtext('id')
            if identifier is None:
                raise ValueError('A layer has no id')
            entry = {
                "name": layer.findtext('layername'),
                "type": layer.get('type'),
                "provider": layer.findtext('provider'),
                "source": sanitize(layer.findtext('datasource', '')),
                "crs": layer.findtext('srs/spatialrefsys/authid'),
            }
            node = tree.get(identifier)
            if node is not None:
                entry['is_visible'] = node.get('checked') == 'Qt::Checked'
            opacity = layer.findtext('layerOpacity')
            if opacity is not None:
                entry['opacity'] = float(opacity)
            layers[identifier] = entry
        order = [
            node.get('id')
            for node in root.iter('layer-tree-layer')
        ]
        out = {
            "crs": root.findtext('projectCrs/spatialrefsys/authid'),
            "layers": layers,
            "order": order,
        }
        return out


class JGISMetadata(Metadata):
    name: ClassVar[str] = 'jgis'
    suffixes: ClassVar[tuple[str, ...]] = ('.jgis',)
    max_bytes: ClassVar[int | None] = 64 * 2**20
    named: ClassVar[tuple[str, ...]] = ('layers', 'sources')
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('projection',): 'map projection {old} → {new}',
        ('layers', '*'): "layer '{label}' {verb}",
        ('layers', '*', 'name'): "layer renamed '{old}' → '{new}'",
        ('layers', '*', 'is_visible'): "layer '{label}' visible {old} → {new}",
        ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
        ('sources', '*'): "source '{label}' {verb}",
        ('sources', '*', 'path'): "source '{label}' path changed",
        ('order',): 'layer order changed',
    }

    @staticmethod
    def flatten(tree: list) -> list[str]:
        out = []
        for item in tree:
            if isinstance(item, dict):
                out.extend(JGISMetadata.flatten(item.get('layers', [])))
            else:
                out.append(item)
        return out

    def read(self) -> Summary:
        text = self._asset.path.read_text(encoding='utf-8')
        project = json.loads(text)
        layers = {}
        saved = project.get('layers', {})
        for identifier, layer in saved.items():
            parameters = layer.get('parameters', {})
            layers[identifier] = {
                'name': layer.get('name'),
                'type': layer.get('type'),
                'is_visible': layer.get('visible'),
                'source': parameters.get('source'),
                'opacity': parameters.get('opacity'),
            }
        sources = {}
        saved = project.get('sources', {})
        for identifier, source in saved.items():
            parameters = source.get('parameters', {})
            location = (
                parameters.get('path')
                or parameters.get('url')
                or ''
            )
            sources[identifier] = {
                'name': source.get('name'),
                'type': source.get('type'),
                'path': sanitize(str(location)),
            }
        options = project.get('options', {})
        out = {
            'schema': project.get('schemaVersion'),
            'projection': options.get('projection'),
            'layers': layers,
            'sources': sources,
            'order': self.flatten(project.get('layerTree', [])),
        }
        return out
