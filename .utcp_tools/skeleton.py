import sys, ast

def python_skeleton(code: str) -> str:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.body = [ast.Expr(value=ast.Constant(value=...))]
    return ast.unparse(tree)

if __name__ == "__main__":
    file_path = sys.argv[1]
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    if file_path.endswith(".py"):
        print(python_skeleton(content))
    else:
        print(content)