"""The site's licence file: every wheel's text, or no build."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from devtools.site.build_site import licenses_text


def _wheel(path: Path, *, licence: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(
            "pkg-1.0.dist-info/METADATA",
            "Name: pkg\nVersion: 1.0\nLicense-Expression: MIT\n",
        )
        if licence:
            wheel.writestr("pkg-1.0.dist-info/licenses/LICENSE", "the licence text")
        wheel.writestr(
            "pkg-1.0.dist-info/sboms/pkg.cyclonedx.json",
            json.dumps(
                {
                    "components": [
                        {
                            "name": "crate",
                            "version": "2.0",
                            "licenses": [{"expression": "Apache-2.0"}],
                        }
                    ]
                }
            ),
        )
    return path


def test_the_file_holds_each_wheels_text_crates_and_the_runtime(tmp_path: Path) -> None:
    text = licenses_text("314.0.7", [_wheel(tmp_path / "pkg-1.0-any.whl")])
    assert "pkg 1.0 (pkg-1.0-any.whl)" in text
    assert "Licence: MIT" in text
    assert "the licence text" in text
    assert "crate 2.0: Apache-2.0" in text
    assert "Mozilla Public License" in text
    assert "tree/314.0.7" in text


def test_a_wheel_with_no_licence_file_stops_the_build(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "pkg-1.0-any.whl", licence=False)
    with pytest.raises(SystemExit, match="carries no licence file"):
        licenses_text("314.0.7", [wheel])
