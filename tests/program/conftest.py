import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures" / "programs" / "solid"))

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def make_ctx(tmp_path):
    from shelf_matrix.program.base import ProgramContext
    from shelf_matrix.program.fonts import FontCache

    def make(params=None, globals=None, data=None):
        return ProgramContext(
            params=params or {}, globals=globals or {}, data=data or {},
            fonts=FontCache(REPO / "fonts", tmp_path / "fontcache"),
        )
    return make


@pytest.fixture
def client_config_file(tmp_path):
    p = tmp_path / "client.toml"
    p.write_text(
        'server_url = "ws://localhost:1"\n'
        'token = "t"\n'
        f'library_dir = "{tmp_path / "lib"}"\n'
        f'programs_dir = "{tmp_path / "programs"}"\n'
        f'fonts_dir = "{REPO / "fonts"}"\n'
        f'state_path = "{tmp_path / "state" / "state.json"}"\n'
    )
    return p
