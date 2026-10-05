from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.dir import build, match
from trail.markers import canonical

PROJECT = Path('/home/user/project')


def decide(
        lines: Sequence[str],
        path: Path,
        project: Path = PROJECT,
) -> str | None:
    dirs, scopes = build(lines, project)
    return match(path, dirs, scopes)


def tracks(
        lines: Sequence[str],
        path: Path,
        project: Path = PROJECT,
) -> bool:
    line = decide(lines, path, project)
    return (
        line is not None
        and not line.startswith('!')
    )


class TestMatching:
    """The matching table of `notes/scopes-refactor-review.md` §8."""

    def test_a_root_scope_applies_beneath_a_root(self) -> None:
        assert decide(['./', '*.csv'], PROJECT / 'a.csv') == '*.csv'
        assert decide(['./', '*.csv'], PROJECT / 'deep' / 'er' / 'a.csv') == '*.csv'

    def test_a_root_scope_does_not_apply_in_a_dir_that_is_not_a_root(self) -> None:
        lines = ['./', '*.csv', '/mnt/data/*.tif']
        assert not tracks(lines, Path('/mnt/data/a.csv'))
        assert tracks(lines, Path('/mnt/data/a.tif'))

    def test_a_dir_scope_takes_only_direct_children(self) -> None:
        assert not tracks(['/mnt/data/*.tif'], Path('/mnt/data/raw/b.tif'))

    def test_a_double_star_reaches_any_depth_and_none(self) -> None:
        lines = ['/mnt/data/**/*.tif']
        assert tracks(lines, Path('/mnt/data/raw/b.tif'))
        assert tracks(lines, Path('/mnt/data/b.tif'))

    def test_a_dir_scope_decides_before_the_root_scopes(self) -> None:
        lines = ['./', '*.csv', '!outputs/**']
        assert decide(lines, PROJECT / 'outputs' / 'a.csv') == '!outputs/**'
        assert not tracks(lines, PROJECT / 'outputs' / 'a.csv')

    def test_the_last_matching_line_of_a_dir_wins(self) -> None:
        path = PROJECT / 'outputs' / 'final_1.png'
        assert tracks(['!outputs/**', 'outputs/final_*.png'], path)
        assert not tracks(['outputs/final_*.png', '!outputs/**'], path)

    def test_a_home_scope_is_anchored(self) -> None:
        lines = ['~/Downloads/prefix_*.png']
        downloads = Path.home() / 'Downloads'
        assert tracks(lines, downloads / 'prefix_a.png')
        assert not tracks(lines, downloads / 'sub' / 'prefix_b.png')

    def test_a_dir_outside_the_project(self) -> None:
        lines = ['../dir/*.png']
        assert list(build(lines, PROJECT)[0]) == [Path('/home/user/dir')]
        assert tracks(lines, Path('/home/user/dir/a.png'))
        assert not tracks(lines, PROJECT / 'a.png')

    def test_case_is_ignored_on_both_sides(self) -> None:
        assert tracks(['./', '*.csv'], PROJECT / 'A.CSV')
        assert tracks(['./', '*.CSV'], PROJECT / 'a.csv')
        assert tracks(['Data/*.PNG'], PROJECT / 'Data' / 'b.png')

    def test_dot_star_takes_every_top_level_file(self) -> None:
        lines = ['./*']
        assert tracks(lines, PROJECT / 'notes.ipynb')
        assert tracks(lines, PROJECT / '.env')
        assert not tracks(lines, PROJECT / 'data' / 'a.csv')

    def test_nothing_outside_a_dir_is_tracked(self) -> None:
        assert decide(['./', '*.csv'], Path('/elsewhere/a.csv')) is None
        assert decide(['*.csv'], PROJECT / 'a.csv') is None


class TestRoots:
    """`./` and `./*`, and a dir with and without its directory marker."""

    files = (
        'a.csv',
        'data/a.csv',
        'notes.ipynb',
        '.env',
    )

    def tracked(self, lines: Sequence[str]) -> list[str]:
        return [
            name
            for name in self.files
            if tracks(lines, PROJECT / name)
        ]

    def test_a_root_versus_every_top_level_file(self) -> None:
        assert self.tracked(['./', '*.csv']) == ['a.csv', 'data/a.csv']
        assert self.tracked(['./*', '*.csv']) == ['a.csv', 'notes.ipynb', '.env']

    def test_a_dir_takes_the_root_scopes_only_with_its_marker(self) -> None:
        directory = Path('/dir')
        without = ['*.csv', '/dir/*.png']
        assert not tracks(without, directory / 'a.csv')
        assert tracks(without, directory / 'a.png')
        marked = ['*.csv', '/dir/', '/dir/*.png']
        assert tracks(marked, directory / 'a.csv')
        assert tracks(marked, directory / 'a.png')

    def test_a_subtree_is_taken_out_and_partly_back_in(self) -> None:
        lines = ['./', '*.csv', '!data/images/**', 'data/images/*.png']
        assert tracks(lines, PROJECT / 'a.csv')
        assert tracks(lines, PROJECT / 'data' / 'a.csv')
        assert not tracks(lines, PROJECT / 'data' / 'images' / 'a.csv')
        assert tracks(lines, PROJECT / 'data' / 'images' / 'a.png')


class TestCanonical:
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
            assert canonical('csv', project, project) == '*.csv'
            assert canonical('.csv', project, project) == '*.csv'
            assert canonical('.tar.gz', project, project) == '*.tar.gz'
            assert canonical('*.csv', project, project) == '*.csv'
            assert canonical(' csv ', project, project) == '*.csv'

    def test_a_double_star_without_a_slash_is_dropped(self) -> None:
        with self.project() as (project, _):
            assert canonical('**/*.csv', project, project) == '*.csv'
            assert canonical('**/requirements*.txt', project, project) == 'requirements*.txt'

    def test_an_existing_directory_gets_its_slash(self) -> None:
        with self.project() as (project, _):
            assert canonical('data', project, project) == 'data/'
            assert canonical('data/raw', project, project) == 'data/raw/'
            assert canonical('.', project, project) == './'
            assert canonical(str(project), project, project) == './'

    def test_a_leading_dot_slash_goes_once_the_rest_has_a_slash(self) -> None:
        with self.project() as (project, _):
            assert canonical('./data/raw/*.tif', project, project) == 'data/raw/*.tif'
            assert canonical('./data/', project, project) == 'data/'
            assert canonical('./a.csv', project, project) == './a.csv'
            assert canonical('./*', project, project) == './*'

    def test_paths_are_written_from_the_project_then_from_home(self) -> None:
        with self.project() as (project, home):
            assert canonical(str(project / 'data' / '*.png'), project, project) == 'data/*.png'
            # relative input is read from the working directory
            assert canonical('raw/*.tif', project, project / 'data') == 'data/raw/*.tif'
            assert canonical(f'{home}/Downloads/x_*.png', project, project) == (
                '~/Downloads/x_*.png'
            )
            assert canonical('~/Downloads/', project, project) == '~/Downloads/'
            assert canonical('/mnt/data/*.tif', project, project) == '/mnt/data/*.tif'

    def test_a_single_word_is_a_path_if_one_exists(self) -> None:
        with self.project() as (project, _):
            assert canonical('notes.md', project, project) == './notes.md'
            assert canonical('notes.txt', project, project) == '*.notes.txt'
            # './' forces the path reading, and a leading dot the extension
            assert canonical('./notes.txt', project, project) == './notes.txt'
            assert canonical('.md', project, project) == '*.md'

    def test_a_bang_is_kept(self) -> None:
        with self.project() as (project, _):
            assert canonical('!.tmp', project, project) == '!*.tmp'
            assert canonical('!data/*.tmp', project, project) == '!data/*.tmp'
            # an excluded directory stays anchored, rather than becoming a root scope
            assert canonical('!data', project, project) == '!./data/'

    def test_nothing_is_not_a_marker(self) -> None:
        with self.project() as (project, _):
            for text in ('', '!', '**/'):
                with pytest.raises(ValueError):
                    canonical(text, project, project)


class TestReprs:
    MARKERS = './\n*.csv\ndata/\ndata/*.png\ndata/raw/*.tif\n'

    def test_dirs_and_scopes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.markers').write_text(self.MARKERS, encoding='utf-8')
            trail = Trail(root)
            assert repr(trail.dirs) == '\n'.join(
                [
                    'Dirs (3)',
                    '    0. Dir',
                    f'        path: {root}',
                    '        root: True',
                    '        scopes: []',
                    '    1. Dir',
                    f'        path: {root}/data',
                    '        root: True',
                    '        scopes: [',
                    '            ./*.png,',
                    '        ]',
                    '    2. Dir',
                    f'        path: {root}/data/raw',
                    '        root: False',
                    '        scopes: [',
                    '            ./*.tif,',
                    '        ]',
                ]
            )
            assert list(trail.scopes) == ['*.csv']

    def test_a_dir_outside_the_project(self) -> None:
        [directory] = build(['/mnt/data/images/train_*.png'], PROJECT)[0].values()
        assert repr(directory) == (
            'Dir\n'
            '    path: /mnt/data/images\n'
            '    root: False\n'
            '    scopes: [\n'
            '        ./train_*.png,\n'
            '    ]'
        )

    def test_an_exclusion_reads_from_its_dir(self) -> None:
        [directory] = build(['!outputs/**', 'outputs/final_*.png'], PROJECT)[0].values()
        assert directory.scopes.relative == ('!./**', './final_*.png')
        assert list(directory.scopes) == ['!outputs/**', 'outputs/final_*.png']


if __name__ == "__main__":
    for suite in (TestMatching(), TestRoots(), TestCanonical(), TestReprs()):
        for name in dir(suite):
            if name.startswith('test_'):
                getattr(suite, name)()
                print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
