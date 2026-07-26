"""
Archive — deprecated modules preserved for reference only.

.. deprecated::
    Archived modules are **not importable**. They are kept in this
    directory for historical reference and must not be imported from
    application code.

Importing any submodule from this package will raise an :exc:`ImportError`
to prevent accidental use of deprecated functionality.

Example:
    >>> from hledac.universal.archive.coordinators_deprecated_2026_07_15 import swarm_coordinator
    ImportError: archived modules are not importable

"""
raise ImportError("archived modules are not importable")
