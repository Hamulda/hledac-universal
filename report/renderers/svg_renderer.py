"""
Uses graphviz `dot` command-line tool for SVG generation.
Subprocess invocation: dot -Tsvg -o output.svg
M1 8GB: graphviz is a system binary, not Python bindings — minimal RAM.

Fails gracefully if graphviz is not installed.
"""
import shutil
import subprocess
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from pathlib import Path
__all__ = ['SVGRenderer', 'GRAPHVIZ_AVAILABLE']
GRAPHVIZ_AVAILABLE: bool | None = None

def _check_graphviz() -> bool:
    """Check if graphviz dot binary is available."""
    global GRAPHVIZ_AVAILABLE
    if GRAPHVIZ_AVAILABLE is not None:
        return GRAPHVIZ_AVAILABLE
    GRAPHVIZ_AVAILABLE = shutil.which('dot') is not None
    return GRAPHVIZ_AVAILABLE

class SVGRenderer:
    """
    Renders graph data to SVG using graphviz dot binary.

    Graph input: dict with 'nodes' and 'edges' lists,
    or an object with get_nodes()/get_edges() methods.

    Falls back to Mermaid text if graphviz unavailable.
    """
    __slots__ = tuple(('_graphviz_available',))

    def __init__(self) -> None:
        self._graphviz_available = _check_graphviz()

    def render(self, graph_data: Any) -> str:
        """Render graph to SVG string."""
        if not self._graphviz_available:
            return self._render_mermaid_fallback(graph_data)
        nodes = self._extract_nodes(graph_data)
        edges = self._extract_edges(graph_data)
        dot_source = self._build_dot_graph(nodes, edges)
        try:
            result = subprocess.run(['dot', '-Tsvg'], input=dot_source.encode('utf-8'), capture_output=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.decode('utf-8')
        except (subprocess.SubprocessError, OSError):  # noqa: BLE001
            pass
        return self._render_mermaid_fallback(graph_data)

    def render_to_file(self, graph_data: Any, path: Path | str) -> Path:
        """Render graph to SVG file."""
        from pathlib import Path as P
        path = P(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self._graphviz_available:
            svg = self._render_mermaid_fallback(graph_data)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(svg)
            return path
        nodes = self._extract_nodes(graph_data)
        edges = self._extract_edges(graph_data)
        dot_source = self._build_dot_graph(nodes, edges)
        try:
            proc = subprocess.Popen(['dot', '-Tsvg', '-o', str(path)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            _stdout, _stderr = proc.communicate(input=dot_source.encode('utf-8'), timeout=30)
            if proc.returncode == 0 and path.exists():
                return path
        except (subprocess.SubprocessError, OSError):  # noqa: BLE001
            pass
        svg = self._render_mermaid_fallback(graph_data)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(svg)
        return path

    def _extract_nodes(self, graph_data: Any) -> list[dict[str, Any]]:
        """Extract nodes from graph data."""
        if isinstance(graph_data, dict):
            return graph_data.get('nodes', [])
        if hasattr(graph_data, 'get_nodes'):
            nodes = graph_data.get_nodes()
            if callable(nodes):
                return nodes()
            return list(nodes) if nodes else []
        if hasattr(graph_data, 'nodes'):
            nodes = graph_data.nodes
            if callable(nodes):
                return nodes()
            return list(nodes) if nodes else []
        return []

    def _extract_edges(self, graph_data: Any) -> list[dict[str, Any]]:
        """Extract edges from graph data."""
        if isinstance(graph_data, dict):
            return graph_data.get('edges', [])
        if hasattr(graph_data, 'get_edges'):
            edges = graph_data.get_edges()
            if callable(edges):
                return edges()
            return list(edges) if edges else []
        if hasattr(graph_data, 'edges'):
            edges = graph_data.edges
            if callable(edges):
                return edges()
            return list(edges) if edges else []
        return []

    def _build_dot_graph(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
        """Build graphviz DOT source from nodes/edges."""
        lines = ['digraph {', '  rankdir=LR;', '  node [shape=box, style=rounded];']
        rendered: set[str] = set()
        for node in nodes[:200]:
            if isinstance(node, dict):
                node_id = node.get('id', node.get('ioc_value', 'unnamed'))
                label = node.get('label', node.get('ioc_value', ''))
                ntype = node.get('type', 'ioc')
            else:
                node_id = str(node)
                label = str(node)
                ntype = 'ioc'
            safe_id = self._safe_id(node_id)
            if safe_id in rendered:
                continue
            rendered.add(safe_id)
            escaped_label = label.replace('"', '\\"')[:60]
            lines.append(f'''  {safe_id} [label="{escaped_label}", shape={('ellipsis' if ntype == 'domain' else 'box')}];''')
        for edge in edges[:400]:
            if isinstance(edge, dict):
                src = edge.get('source', edge.get('src', ''))
                dst = edge.get('target', edge.get('dst', ''))
                rel = edge.get('relation', edge.get('type', ''))
            else:
                src, dst, rel = (str(edge), '', '')
            safe_src = self._safe_id(src)
            safe_dst = self._safe_id(dst)
            if not safe_src or not safe_dst:
                continue
            if safe_src == safe_dst:
                continue
            rel_safe = rel.replace('"', '\\"')[:30] if rel else ''
            if rel_safe:
                lines.append(f'  {safe_src} -> {safe_dst} [label="{rel_safe}"];')
            else:
                lines.append(f'  {safe_src} -> {safe_dst};')
        lines.append('}')
        return '\n'.join(lines)

    @staticmethod
    def _safe_id(node_id: str) -> str:
        """Make safe DOT/node ID."""
        import re
        safe = re.sub('[^a-zA-Z0-9_]', '_', str(node_id))[:40]
        return safe or ''

    def _render_mermaid_fallback(self, graph_data: Any) -> str:
        """Render as Mermaid SVG when graphviz unavailable."""
        nodes = self._extract_nodes(graph_data)
        self._extract_edges(graph_data)
        lines = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'>", '<style>text{font-family:system-ui,sans-serif;font-size:12px;fill:#e6edf3}rect{fill:#161b22;stroke:#30363d;rx:4}</style>', "<rect width='800' height='600' fill='#0d1117'/>"]
        rendered: set[str] = set()
        count = 0
        for node in nodes[:20]:
            if isinstance(node, dict):
                node_id = node.get('id', node.get('ioc_value', ''))
                label = node.get('label', node.get('ioc_value', ''))[:20]
            else:
                node_id = str(node)
                label = str(node)[:20]
            safe_id = self._safe_id(node_id)
            if safe_id in rendered:
                continue
            rendered.add(safe_id)
            x = 60 + count % 4 * 180
            y = 60 + count // 4 * 80
            lines.append(f"<rect x='{x}' y='{y}' width='140' height='36'/><text x='{x + 70}' y='{y + 22}' text-anchor='middle'>{label}</text>")
            count += 1
            if count >= 20:
                break
        lines.append('</svg>')
        return '\n'.join(lines)