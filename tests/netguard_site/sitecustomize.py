"""Test-only ``sitecustomize``: block the network in CLI subprocesses.

``tests.fakes.run_cli`` puts this directory first on ``PYTHONPATH``, so every
``python -m domainhack`` a unit test starts runs with ``tests/netguard.py``
installed. Any interpreter-provided ``sitecustomize`` this one shadows is
still executed afterwards.
"""

import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str) -> None:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


def _install_guard() -> None:
    _load("_domainhack_netguard", os.path.join(os.path.dirname(_HERE), "netguard.py"))
    sys.modules["_domainhack_netguard"].NetworkGuard().install()


def _chain_shadowed_sitecustomize() -> None:
    others = [p for p in sys.path if os.path.abspath(p or os.curdir) != _HERE]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", others)
    if spec is not None and spec.origin is not None:
        _load("_shadowed_sitecustomize", spec.origin)


_chain_shadowed_sitecustomize()
_install_guard()
