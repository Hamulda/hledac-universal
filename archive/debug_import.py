import sys

print('sys.path[:3]:', sys.path[:3])
from importlib.machinery import PathFinder  # noqa: E402

spec = PathFinder.find_spec('hledac.universal', path=None)
print('PathFinder spec:', spec)
import hledac.universal  # noqa: E402

print('path:', getattr(hledac.universal, '__path__', 'MISSING'))
print('file:', getattr(hledac.universal, '__file__', 'MISSING'))
