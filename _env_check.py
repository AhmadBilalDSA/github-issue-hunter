import importlib

for m in ("numpy", "pandas", "pyarrow", "fastparquet", "pytest", "tabulate"):
    try:
        mod = importlib.import_module(m)
        print(m, getattr(mod, "__version__", "?"))
    except Exception as exc:
        print(m, "MISSING:", type(exc).__name__)
