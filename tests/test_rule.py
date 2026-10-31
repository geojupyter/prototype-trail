from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.offtrail import OffTrail
from trail.ontrail import OnTrail
from trail.rule import Rule


class TestRuleParse:
    project = Path('/home/user/project')

    def test_roots(self) -> None:
        project = self.project
        assert Rule('./').parse(project) == (project, '')
        assert Rule('data/').parse(project) == (project / 'data', '')
        assert Rule('/mnt/data/').parse(project) == (Path('/mnt/data'), '')
        assert Rule('~/Downloads/').parse(project) == (Path.home() / 'Downloads', '')

    def test_root_scopes(self) -> None:
        for line in ('*.csv', '!requirements*.txt', 'Makefile'):
            assert Rule(line).parse(self.project) == (None, line)

    def test_dir_scopes_belong_to_the_directory_before_the_first_wildcard(self) -> None:
        project = self.project
        assert Rule('data/*.png').parse(project) == (project / 'data', '/*.png')
        assert Rule('./a.csv').parse(project) == (project, '/a.csv')
        assert Rule('./*').parse(project) == (project, '/*')
        assert Rule('data/raw/b.tif').parse(project) == (project / 'data' / 'raw', '/b.tif')
        assert Rule('data/*/b.tif').parse(project) == (project / 'data', '/*/b.tif')
        assert Rule('/mnt/data/**/*.tif').parse(project) == (Path('/mnt/data'), '/**/*.tif')
        assert Rule('../dir/*.png').parse(project) == (project.parent / 'dir', '/*.png')
        assert Rule('~/Downloads/prefix_*.png').parse(project) == (
            Path.home() / 'Downloads',
            '/prefix_*.png',
        )

    def test_an_exclusion_keeps_its_bang_in_the_pattern(self) -> None:
        project = self.project
        assert Rule('!outputs/**').parse(project) == (project / 'outputs', '!/**')
        # without an anchor, an excluded directory is excluded at any depth, as in gitignore
        assert Rule('!data/').parse(project) == (None, '!data/')
        assert Rule('!./data/').parse(project) == (project, '!/data/')

    def test_a_line_that_is_not_a_rule_is_refused(self) -> None:
        for line in ('!', '! ', 'data/\\'):
            with pytest.raises(ValueError):
                Rule(line).parse(self.project)

    def test_kinds(self) -> None:
        project = self.project
        assert Rule('./').kind(project) == 'root'
        assert Rule('*.csv').kind(project) == 'root scope'
        assert Rule('!*.tmp').kind(project) == 'root scope · exclude'
        assert Rule('data/*.png').kind(project) == 'dir scope · not subfolders'
        assert Rule('data/**/*.png').kind(project) == 'dir scope'
        assert Rule('!outputs/**').kind(project) == 'dir scope · exclude'
        assert Rule('!').kind(project) == 'invalid'


class TestRuleFiles:
    def test_collections_keep_rule_methods_after_edits_and_reload(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            trail = Trail(root)
            assert trail.ontrail[0].kind(root) == 'root'
            assert trail.offtrail[0].parse(root) == (None, '.*')
            trail.ontrail.add('csv')
            assert trail.ontrail[-1].kind(root) == 'root scope'
            trail.ontrail.replace(['./', '*.txt'])
            trail.offtrail.replace(['.*', '!.env'])
            restored = Trail(root)
            for rules in (restored.ontrail, restored.offtrail):
                assert all(
                    isinstance(rule, Rule)
                    for rule in rules
                )
            canonical = Rule('**/*.csv').canonicalize(root, root)
            assert isinstance(canonical, Rule)
            assert canonical.parse(root) == (None, '*.csv')

    def test_each_file_uses_its_own_presets(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            trail = Trail(root)
            assert OnTrail.available() == ['default', 'general', 'geospatial', 'ml']
            assert OffTrail.available() == ['default']
            (root / '.offtrail').unlink()
            with pytest.raises(FileNotFoundError, match="available: \\['default'\\]"):
                trail.offtrail.txt.create('geospatial')
            assert not (root / '.offtrail').exists()
            trail.offtrail.txt.create('default')
            assert (root / '.offtrail').read_text(encoding='utf-8') == '.*\n'
            assert list(trail.offtrail) == ['.*']



class TestRuleCanonicalization:
    """Every input step 2 of the plan lists."""

    @staticmethod
    @contextmanager
    def project() -> Iterator[tuple[Path, Path]]:
        with (
            TemporaryDirectory() as temporary,
            TemporaryDirectory() as home,
            pytest.MonkeyPatch.context() as monkeypatch,
        ):
            monkeypatch.setenv('HOME', home)
            project = Path(temporary).resolve()
            (project / 'data' / 'raw').mkdir(parents=True)
            (project / 'notes.md').write_text('a\n', encoding='utf-8')
            yield project, Path(home).resolve()

    def test_extensions(self) -> None:
        with self.project() as (project, _):
            assert Rule('csv').canonicalize(project, project) == '*.csv'
            assert Rule('.csv').canonicalize(project, project) == '*.csv'
            assert Rule('.tar.gz').canonicalize(project, project) == '*.tar.gz'
            assert Rule('*.csv').canonicalize(project, project) == '*.csv'
            assert Rule(' csv ').canonicalize(project, project) == '*.csv'

    def test_a_double_star_without_a_slash_is_dropped(self) -> None:
        with self.project() as (project, _):
            assert Rule('**/*.csv').canonicalize(project, project) == '*.csv'
            assert Rule('**/requirements*.txt').canonicalize(project, project) == 'requirements*.txt'

    def test_an_existing_directory_gets_its_slash(self) -> None:
        with self.project() as (project, _):
            assert Rule('data').canonicalize(project, project) == 'data/'
            assert Rule('data/raw').canonicalize(project, project) == 'data/raw/'
            assert Rule('.').canonicalize(project, project) == './'
            assert Rule(str(project)).canonicalize(project, project) == './'

    def test_a_dirasset_gets_no_slash(self) -> None:
        with self.project() as (project, _):
            (project / 'cube.zarr').mkdir()
            (project / 'data' / 'scene.SAFE').mkdir()
            assert Rule('cube.zarr').canonicalize(project, project) == './cube.zarr'
            assert Rule('data/scene.SAFE').canonicalize(project, project) == 'data/scene.SAFE'
            assert Rule('../cube.zarr').canonicalize(project, project / 'data') == './cube.zarr'

    def test_a_dotted_folder_keeps_its_slash(self) -> None:
        with self.project() as (project, _):
            for name in ('results.v2', 'python3.12', '2026.10'):
                (project / name).mkdir()
                assert Rule(name).canonicalize(project, project) == f'{name}/'

    def test_a_leading_dot_slash_goes_once_the_rest_has_a_slash(self) -> None:
        with self.project() as (project, _):
            assert Rule('./data/raw/*.tif').canonicalize(project, project) == 'data/raw/*.tif'
            assert Rule('./data/').canonicalize(project, project) == 'data/'
            assert Rule('./a.csv').canonicalize(project, project) == './a.csv'
            assert Rule('./*').canonicalize(project, project) == './*'

    def test_paths_are_written_from_the_project_then_from_home(self) -> None:
        with self.project() as (project, home):
            assert Rule(str(project / 'data' / '*.png')).canonicalize(project, project) == 'data/*.png'
            # relative input is read from the working directory
            assert Rule('raw/*.tif').canonicalize(project, project / 'data') == 'data/raw/*.tif'
            assert Rule(f'{home}/Downloads/x_*.png').canonicalize(project, project) == (
                '~/Downloads/x_*.png'
            )
            assert Rule('~/Downloads/').canonicalize(project, project) == '~/Downloads/'
            assert Rule('/mnt/data/*.tif').canonicalize(project, project) == '/mnt/data/*.tif'

    def test_a_single_word_is_a_path_if_one_exists(self) -> None:
        with self.project() as (project, _):
            assert Rule('notes.md').canonicalize(project, project) == './notes.md'
            assert Rule('notes.txt').canonicalize(project, project) == '*.notes.txt'
            # './' forces the path reading, and a leading dot the extension
            assert Rule('./notes.txt').canonicalize(project, project) == './notes.txt'
            assert Rule('.md').canonicalize(project, project) == '*.md'

    def test_a_bang_is_kept(self) -> None:
        with self.project() as (project, _):
            assert Rule('!.tmp').canonicalize(project, project) == '!*.tmp'
            assert Rule('!data/*.tmp').canonicalize(project, project) == '!data/*.tmp'
            # an excluded directory stays anchored, rather than becoming a root scope
            assert Rule('!data').canonicalize(project, project) == '!./data/'

    def test_nothing_is_not_a_rule(self) -> None:
        with self.project() as (project, _):
            for text in ('', '!', '**/'):
                with pytest.raises(ValueError):
                    Rule(text).canonicalize(project, project)
