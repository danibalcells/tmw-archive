"""Stub for CoverHunterMPS (https://github.com/alanngnet/CoverHunterMPS).

CoverHunterMPS is not on PyPI. This module provides the interface used by
pipeline/features/coverhunter.py so the codebase type-checks and imports
cleanly when the real library is not on sys.path.

All callables raise NotImplementedError at runtime.
See MODEL_SETUP.md in this directory for installation instructions.
"""

from __future__ import annotations

from typing import Any


class Model:
    """Stub matching CoverHunterMPS src.model.Model."""

    _MSG = (
        "CoverHunterMPS is not installed. "
        "See pipeline/vendor/coverhunter_mps/MODEL_SETUP.md for setup instructions. "
        "Set COVERHUNTER_SRC_DIR to the root of your CoverHunterMPS clone."
    )

    def __init__(self, hp: dict) -> None:
        raise NotImplementedError(self._MSG)

    def to(self, device: Any) -> "Model":
        raise NotImplementedError(self._MSG)

    def eval(self) -> "Model":
        raise NotImplementedError(self._MSG)

    def load_model_parameters(
        self,
        checkpoint_dir: str,
        epoch_num: int = -1,
        device: str = "mps",
        advanced: bool = False,
    ) -> int:
        raise NotImplementedError(self._MSG)

    def inference(self, feat: Any) -> tuple[Any, Any]:
        raise NotImplementedError(self._MSG)
