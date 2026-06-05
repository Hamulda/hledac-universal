import sys

print('sys.path[:3]:', sys.path[:3])
from importlib.machinery import PathFinder

spec = PathFinder.find_spec('hledac.universal', path=None)
print('PathFinder spec:', spec)
import hledac.universal

print('path:', getattr(hledac.universal, '__path__', 'MISSING'))
print('file:', getattr(hledac.universal, '__file__', 'MISSING'))
