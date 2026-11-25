import importlib
from ..orders import place


def load(name):
    importlib.import_module("shop.plugins.extra")
    return importlib.import_module(name)
