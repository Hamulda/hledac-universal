

# DEPRECATED: dataclass(transform=...) not available in Python 3.14.6
# Use msgspec.Struct for hot paths, keep __post_init__ for validation.
# See: Sprint F265-U5 analysis in memory.
