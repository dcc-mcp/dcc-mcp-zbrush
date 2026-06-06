from importlib.metadata import version as _version

try:
    __version__ = _version("dcc-mcp-zbrush")
except Exception:
    __version__ = "0.0.0"
