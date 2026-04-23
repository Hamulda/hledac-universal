#!/usr/bin/env python3
"""
API Documentation Generator for Hledac v5.2 Elite Platform

This tool automatically generates comprehensive API documentation by parsing the Python codebase,
extracting classes, methods, functions, docstrings, and type hints.

Features:
- Automatic discovery of all Python modules in hledac package
- Extract class hierarchies and inheritance relationships
- Parse method signatures with type hints
- Extract comprehensive docstrings
- Generate cross-references and examples
- Create categorized documentation (agents, core, intelligence, etc.)
- Auto-generate usage examples and code snippets
"""

import ast
import asyncio
import importlib
import inspect
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import json


@dataclass
class APIClass:
    """Represents a Python class with its documentation."""
    name: str
    module: str
    docstring: str
    methods: List['APIMethod']
    properties: List['APIProperty']
    base_classes: List[str]
    decorators: List[str]
    file_path: str
    line_number: int


@dataclass
class APIMethod:
    """Represents a Python method or function."""
    name: str
    signature: str
    docstring: str
    return_type: str
    parameters: List['APIParameter']
    decorators: List[str]
    is_async: bool
    is_static: bool
    is_class_method: bool
    is_property: bool
    line_number: int


@dataclass
class APIParameter:
    """Represents a function parameter."""
    name: str
    type_hint: str
    default_value: str
    is_optional: bool
    description: str


@dataclass
class APIProperty:
    """Represents a class property."""
    name: str
    type_hint: str
    docstring: str
    is_readonly: bool
    line_number: int


@dataclass
class APIModule:
    """Represents a Python module."""
    name: str
    file_path: str
    docstring: str
    classes: List[APIClass]
    functions: List[APIMethod]
    imports: List[str]
    constants: Dict[str, Any]


class APIDocGenerator:
    """Main API documentation generator."""
    
    def __init__(self, package_path: str = "hledac"):
        self.package_path = Path(package_path)
        self.modules: Dict[str, APIModule] = {}
        self.class_hierarchy: Dict[str, List[str]] = defaultdict(list)
        self.cross_references: Dict[str, Set[str]] = defaultdict(set)
        self.doc_categories = {
            "agents": [],
            "core": [],
            "intelligence": [],
            "llm": [],
            "runtime": [],
            "storage": [],
            "monitoring": [],
            "security": [],
            "optimization": [],
            "api": [],
            "utils": []
        }
    
    def discover_modules(self) -> List[Path]:
        """Discover all Python modules in the package."""
        modules = []
        for py_file in self.package_path.rglob("*.py"):
            if py_file.name == "__init__.py" or py_file.name.startswith("."):
                    continue
            # Skip test files and private modules
            if "test" in py_file.name or py_file.name.startswith("_"):
                    continue
            modules.append(py_file)
            return sorted(modules)
    
    def parse_module(self, file_path: Path) -> APIModule:
        """Parse a Python module and extract its API elements."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"Syntax error in {file_path}: {e}")
                return APIModule(
                name=file_path.stem,
                file_path=str(file_path),
                docstring="",
                classes=[],
                functions=[],
                imports=[],
                constants={}
            )
        
        module_name = self.get_module_name(file_path)
        module_docstring = ast.get_docstring(tree) or ""
        
        classes = []
        functions = []
        imports = []
        constants = {}
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                    cls = self.parse_class(node, module_name, str(file_path))
                classes.append(cls)
            elif isinstance(node, ast.FunctionDef) and self.is_top_level_function(node, tree):
                    func = self.parse_function(node, module_name)
                functions.append(func)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports.extend(self.parse_import(node))
            elif isinstance(node, ast.Assign) and self.is_constant_assignment(node):
                    const_values = self.parse_constants(node)
                constants.update(const_values)
        
            return APIModule(
            name=module_name,
            file_path=str(file_path),
            docstring=module_docstring,
            classes=classes,
            functions=functions,
            imports=imports,
            constants=constants
        )
    
    def parse_class(self, node: ast.ClassDef, module_name: str, file_path: str) -> APIClass:
        """Parse a class definition."""
        docstring = ast.get_docstring(node) or ""
        
        methods = []
        properties = []
        
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method = self.parse_function(item, module_name, node.name)
                if method.is_property:
                        properties.append(APIProperty(
                        name=method.name,
                        type_hint=method.return_type,
                        docstring=method.docstring,
                        is_readonly=not any("setter" in d for d in method.decorators),
                        line_number=method.line_number
                    ))
                else:
                    methods.append(method)
            elif isinstance(item, ast.Assign):
                # Handle class-level constants
                for target in item.targets:
                    if isinstance(target, ast.Name):
                            const_name = target.id
                        const_value = ast.unparse(item.value) if hasattr(ast, 'unparse') else str(item.value)
                        # This could be enhanced to extract type information
        
        base_classes = [self.get_class_name(base) for base in node.bases]
        decorators = [self.get_decorator_name(dec) for dec in node.decorator_list]
        
        # Build class hierarchy
        for base in base_classes:
            self.class_hierarchy[base].append(node.name)
        
            return APIClass(
            name=node.name,
            module=module_name,
            docstring=docstring,
            methods=methods,
            properties=properties,
            base_classes=base_classes,
            decorators=decorators,
            file_path=file_path,
            line_number=node.lineno
        )
    
    def parse_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], 
                        module_name: str, class_name: str = None) -> APIMethod:
        """Parse a function or method definition."""
        docstring = ast.get_docstring(node) or ""
        
        # Extract signature
        signature = self.get_function_signature(node)
        
        # Parse parameters
        parameters = []
        for arg in node.args.args:
            param_name = arg.arg
            type_hint = ast.unparse(arg.annotation) if arg.annotation and hasattr(ast, 'unparse') else "Any"
            
            # Check if parameter has default value
            default_idx = len(node.args.args) - len(node.args.defaults)
            is_optional = False
            default_value = ""
            if node.args.defaults:
                    param_idx = node.args.args.index(arg)
                if param_idx >= default_idx:
                        is_optional = True
                    default_idx_corrected = param_idx - default_idx
                    if default_idx_corrected < len(node.args.defaults):
                            default_node = node.args.defaults[default_idx_corrected]
                        default_value = ast.unparse(default_node) if hasattr(ast, 'unparse') else str(default_node)
            
            # Extract parameter description from docstring
            description = self.extract_param_description(docstring, param_name)
            
            parameters.append(APIParameter(
                name=param_name,
                type_hint=type_hint,
                default_value=default_value,
                is_optional=is_optional,
                description=description
            ))
        
        return_type = ast.unparse(node.returns) if node.returns and hasattr(ast, 'unparse') else "Any"
        decorators = [self.get_decorator_name(dec) for dec in node.decorator_list]
        
        is_async = isinstance(node, ast.AsyncFunctionDef)
        is_static = any("staticmethod" in d for d in decorators)
        is_class_method = any("classmethod" in d for d in decorators)
        is_property = any("property" in d for d in decorators)
        
            return APIMethod(
            name=node.name,
            signature=signature,
            docstring=docstring,
            return_type=return_type,
            parameters=parameters,
            decorators=decorators,
            is_async=is_async,
            is_static=is_static,
            is_class_method=is_class_method,
            is_property=is_property,
            line_number=node.lineno
        )
    
    def get_function_signature(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> str:
        """Extract function signature as string."""
        args_str = []
        
        # Regular arguments
        for i, arg in enumerate(node.args.args):
            arg_str = arg.arg
            if arg.annotation and hasattr(ast, 'unparse'):
                    arg_str += f": {ast.unparse(arg.annotation)}"
            
            # Add default values
            if node.args.defaults:
                    default_start = len(node.args.args) - len(node.args.defaults)
                if i >= default_start:
                        default_idx = i - default_start
                    if default_idx < len(node.args.defaults):
                            default_node = node.args.defaults[default_idx]
                        default_value = ast.unparse(default_node) if hasattr(ast, 'unparse') else str(default_node)
                        arg_str += f" = {default_value}"
            
            args_str.append(arg_str)
        
        # Handle *args and **kwargs
        if node.args.vararg:
                vararg_str = f"*{node.args.vararg.arg}"
            if node.args.vararg.annotation and hasattr(ast, 'unparse'):
                    vararg_str += f": {ast.unparse(node.args.vararg.annotation)}"
            args_str.append(vararg_str)
        
        if node.args.kwarg:
                kwarg_str = f"**{node.args.kwarg.arg}"
            if node.args.kwarg.annotation and hasattr(ast, 'unparse'):
                    kwarg_str += f": {ast.unparse(node.args.kwarg.annotation)}"
            args_str.append(kwarg_str)
        
        signature = f"{node.name}({', '.join(args_str)})"
        
        if node.returns and hasattr(ast, 'unparse'):
                signature += f" -> {ast.unparse(node.returns)}"
        
            return signature
    
    def extract_param_description(self, docstring: str, param_name: str) -> str:
        """Extract parameter description from docstring."""
        if not docstring:
                    return ""
        
        # Look for parameter descriptions in various docstring formats
        patterns = [
            rf"{param_name}\s*:\s*([^\n]+)",
            rf"Args\s*{param_name}\s*\(([^)]+)\)\s*:\s*([^\n]+)",
            rf"Parameters\s*{param_name}\s*:\s*([^\n]+)",
            rf":param\s+{param_name}:\s*([^\n]+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, docstring, re.IGNORECASE | re.MULTILINE)
            if match:
                        return match.group(1).strip()
        
            return ""
    
    def get_module_name(self, file_path: Path) -> str:
        """Convert file path to module name."""
        relative_path = file_path.relative_to(self.package_path)
        parts = list(relative_path.parts[:-1])  # Exclude file name
        if parts and parts[-1] == "__pycache__":
                parts = parts[:-1]
            return ".".join(parts) if parts else file_path.stem
    
    def get_class_name(self, node: ast.AST) -> str:
        """Extract class name from AST node."""
        if isinstance(node, ast.Name):
                    return node.id
        elif isinstance(node, ast.Attribute):
                    return f"{node.value.id}.{node.attr}" if hasattr(node.value, 'id') else node.attr
            return "Unknown"
    
    def get_decorator_name(self, node: ast.AST) -> str:
        """Extract decorator name."""
        if isinstance(node, ast.Name):
                    return node.id
        elif isinstance(node, ast.Attribute):
                    return f"{node.value.id}.{node.attr}" if hasattr(node.value, 'id') else node.attr
            return str(node)
    
    def is_top_level_function(self, node: ast.FunctionDef, tree: ast.Module) -> bool:
        """Check if function is defined at module level."""
        # Check if the node is directly in the module's body
        for module_node in tree.body:
            if module_node is node:
                        return True
            return False
    
    def is_constant_assignment(self, node: ast.Assign) -> bool:
        """Check if assignment is a module-level constant."""
        # Simple heuristic: uppercase names and simple values
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                        return True
            return False
    
    def parse_constants(self, node: ast.Assign) -> Dict[str, Any]:
        """Parse constant assignments."""
        constants = {}
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                    try:
                    if hasattr(ast, 'unparse'):
                            value_str = ast.unparse(node.value)
                        # Try to evaluate simple constants
                        try:
                            value = ast.literal_eval(node.value)
                            constants[target.id] = value
                        except (ValueError, SyntaxError):
                            constants[target.id] = value_str
                    else:
                        constants[target.id] = str(node.value)
                except Exception:
                    constants[target.id] = "Unknown"
            return constants
    
    def parse_import(self, node: Union[ast.Import, ast.ImportFrom]) -> List[str]:
        """Parse import statements."""
        imports = []
        if isinstance(node, ast.Import):
                for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
            for alias in node.names:
                imports.append(f"from {module} import {alias.name}")
            return imports
    
    def categorize_module(self, module: APIModule) -> str:
        """Categorize module into documentation sections."""
        path_parts = module.name.split(".")
        
        if any(part in path_parts for part in ["agents"]):
                    return "agents"
        elif any(part in path_parts for part in ["core", "config"]):
                    return "core"
        elif any(part in path_parts for part in ["intelligence", "reasoning", "quantum"]):
                    return "intelligence"
        elif any(part in path_parts for part in ["llm", "model"]):
                    return "llm"
        elif any(part in path_parts for part in ["runtime", "orchestration"]):
                    return "runtime"
        elif any(part in path_parts for part in ["storage", "database"]):
                    return "storage"
        elif any(part in path_parts for part in ["monitoring", "metrics"]):
                    return "monitoring"
        elif any(part in path_parts for part in ["security", "auth", "encrypt"]):
                    return "security"
        elif any(part in path_parts for part in ["performance", "optimization", "memory"]):
                    return "optimization"
        elif any(part in path_parts for part in ["api", "endpoints"]):
                    return "api"
        else:
                return "utils"
    
    def generate_markdown_documentation(self, output_dir: str = "docs"):
        """Generate comprehensive markdown documentation."""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        # Parse all modules
        print("Discovering modules...")
        module_files = self.discover_modules()
        print(f"Found {len(module_files)} modules")
        
        print("Parsing modules...")
        for file_path in module_files:
            print(f"  Parsing {file_path}")
            module = self.parse_module(file_path)
            self.modules[module.name] = module
            
            # Categorize module
            category = self.categorize_module(module)
            self.doc_categories[category].append(module)
        
        # Generate documentation files
        print("Generating documentation...")
        
        # Main API reference
        self.generate_api_reference(output_path)
        
        # Categorized documentation
        for category, modules in self.doc_categories.items():
            if modules:
                    self.generate_category_doc(category, modules, output_path)
        
        # Cross-reference index
        self.generate_cross_reference_index(output_path)
        
        # Examples and usage guides
        self.generate_examples(output_path)
        
        print(f"Documentation generated in {output_path}")
    
    def generate_api_reference(self, output_path: Path):
        """Generate main API reference documentation."""
        content = [
            "# Hledac v5.2 Elite Platform - API Reference",
            "",
            "## Overview",
            "",
            "This API reference provides comprehensive documentation for all classes, methods, and functions in the Hledac platform.",
            "",
            "## Table of Contents",
            ""
        ]
        
        # Add category links
        for category, modules in self.doc_categories.items():
            if modules:
                    category_name = category.replace("_", " ").title()
                content.append(f"- [{category_name}](api/{category}.md)")
        
        content.extend([
            "",
            "## Quick Reference",
            "",
            "### Core Components",
            "",
            "| Component | Description | Module |",
            "|-----------|-------------|--------|"
        ])
        
        # Add core classes summary
        for module in self.modules.values():
            for cls in module.classes:
                if any(name in cls.name.lower() for name in ["agent", "orchestrator", "manager", "system"]):
                        description = cls.docstring.split('\n')[0] if cls.docstring else "No description"
                    content.append(f"| [{cls.name}](api/{self.categorize_module(module)}.md#{cls.name.lower()}) | {description[:100]}... | {module.name} |")
        
        api_content = "\n".join(content)
        
        with open(output_path / "API_REFERENCE_GENERATED.md", 'w', encoding='utf-8') as f:
            f.write(api_content)
    
    def generate_category_doc(self, category: str, modules: List[APIModule], output_path: Path):
        """Generate documentation for a specific category."""
        category_name = category.replace("_", " ").title()
        
        content = [
            f"# {category_name} API Documentation",
            "",
            f"This section contains all {category_name.lower()} components of the Hledac platform.",
            "",
            "## Modules",
            ""
        ]
        
        for module in modules:
            content.append(f"### {module.name}")
            content.append("")
            content.append(f"**File**: `{module.file_path}`")
            content.append("")
            
            if module.docstring:
                    content.append(module.docstring)
                content.append("")
            
            if module.classes:
                    content.append("#### Classes")
                content.append("")
                for cls in module.classes:
                    self.generate_class_doc(cls, content)
            
            if module.functions:
                    content.append("#### Functions")
                content.append("")
                for func in module.functions:
                    self.generate_function_doc(func, content)
            
            if module.constants:
                    content.append("#### Constants")
                content.append("")
                for name, value in module.constants.items():
                    content.append(f"- **{name}**: `{value}`")
                content.append("")
        
        # Create api subdirectory
        api_dir = output_path / "api"
        api_dir.mkdir(exist_ok=True)
        
        with open(api_dir / f"{category}.md", 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
    
    def generate_class_doc(self, cls: APIClass, content: List[str]):
        """Generate documentation for a class."""
        content.append(f"##### {cls.name}")
        content.append("")
        
        if cls.docstring:
                content.append(cls.docstring)
            content.append("")
        
        # Class signature
        content.append("```python")
        if cls.base_classes:
                content.append(f"class {cls.name}({', '.join(cls.base_classes)}):")
        else:
            content.append(f"class {cls.name}:")
        content.append("```")
        content.append("")
        
        # Add inheritance info
        if cls.base_classes:
                content.append("**Inherits from**: " + ", ".join([f"`{base}`" for base in cls.base_classes]))
            content.append("")
        
        # Add decorators
        if cls.decorators:
                content.append("**Decorators**: " + ", ".join([f"`{dec}`" for dec in cls.decorators]))
            content.append("")
        
        # Add properties
        if cls.properties:
                content.append("**Properties**:")
            content.append("")
            for prop in cls.properties:
                content.append(f"- `{prop.name}`: {prop.type_hint}" + (f" - {prop.docstring}" if prop.docstring else ""))
            content.append("")
        
        # Add methods
        if cls.methods:
                content.append("**Methods**:")
            content.append("")
            for method in cls.methods:
                self.generate_method_doc(method, content)
        
        content.append("")
    
    def generate_function_doc(self, func: APIMethod, content: List[str]):
        """Generate documentation for a function."""
        self.generate_method_doc(func, content)
    
    def generate_method_doc(self, method: APIMethod, content: List[str]):
        """Generate documentation for a method or function."""
        # Method signature
        async_prefix = "async " if method.is_async else ""
        static_prefix = "@staticmethod\n" if method.is_static else ""
        classmethod_prefix = "@classmethod\n" if method.is_class_method else ""
        
        content.append(f"###### {method.name}")
        content.append("")
        
        if method.docstring:
                content.append(method.docstring)
            content.append("")
        
        content.append("```python")
        if static_prefix:
                content.append(static_prefix)
        if classmethod_prefix:
                content.append(classmethod_prefix)
        content.append(f"{async_prefix}def {method.signature}")
        content.append("```")
        content.append("")
        
        # Parameters
        if method.parameters:
                content.append("**Parameters**:")
            content.append("")
            for param in method.parameters:
                param_str = f"- `{param.name}`: {param.type_hint}"
                if param.is_optional:
                        param_str += f" (optional, default: `{param.default_value}`)"
                if param.description:
                        param_str += f" - {param.description}"
                content.append(param_str)
            content.append("")
        
        # Return type
        if method.return_type and method.return_type != "Any":
                content.append(f"**Returns**: {method.return_type}")
            content.append("")
        
        # Decorators
        if method.decorators:
                content.append("**Decorators**: " + ", ".join([f"`{dec}`" for dec in method.decorators]))
            content.append("")
        
        content.append("")
    
    def generate_cross_reference_index(self, output_path: Path):
        """Generate cross-reference index."""
        content = [
            "# API Cross-Reference Index",
            "",
            "This index provides quick access to all API elements.",
            "",
            "## Alphabetical Index",
            ""
        ]
        
        # Collect all classes and functions
        all_classes = []
        all_functions = []
        
        for module in self.modules.values():
            for cls in module.classes:
                all_classes.append((cls.name, module.name, cls))
            for func in module.functions:
                all_functions.append((func.name, module.name, func))
        
        # Sort alphabetically
        all_classes.sort(key=lambda x: x[0])
        all_functions.sort(key=lambda x: x[0])
        
        # Classes index
        content.append("### Classes")
        content.append("")
        for name, module_name, cls in all_classes:
            content.append(f"- [{name}](api/{self.categorize_module(self.modules[module_name])}.md#{name.lower()}) - `{module_name}`")
        
        content.append("")
        content.append("### Functions")
        content.append("")
        for name, module_name, func in all_functions:
            content.append(f"- [{name}](api/{self.categorize_module(self.modules[module_name])}.md#{name.lower()}) - `{module_name}`")
        
        with open(output_path / "API_CROSS_REFERENCE.md", 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
    
    def generate_examples(self, output_path: Path):
        """Generate usage examples."""
        examples_dir = output_path / "examples"
        examples_dir.mkdir(exist_ok=True)
        
        # Agent examples
        agent_examples = [
            {
                "title": "Basic Agent Usage",
                "description": "How to use basic search agents",
                "code": '''
from hledac.agents.agent_openalex import OpenAlexAgent
from hledac.models import SearchRequest

# Create agent
agent = OpenAlexAgent()

# Perform search
request = SearchRequest(
    query="machine learning in healthcare",
    limit=10,
    user_id="user123"
)

results = await agent.search(request)
print(f"Found {len(results)} results")
'''
            },
            {
                "title": "Performance-Optimized Agent",
                "description": "How to use next-gen performance-optimized agents",
                "code": '''
from hledac.agents.agent_autonomous_learner import AutonomousLearnerAgent
from hledac.llm.lmstudio_client import LMStudioClient

# Create agent with LM Studio integration
agent = AutonomousLearnerAgent()

# Learn domain expertise
await agent.learn_domain("quantum computing")

# Get expertise level
expertise = await agent.get_expertise_level()
print(f"Expertise level: {expertise}")
'''
            }
        ]
        
        content = [
            "# API Usage Examples",
            "",
            "This section provides practical examples of how to use the Hledac API.",
            "",
            "## Agent Examples",
            ""
        ]
        
        for i, example in enumerate(agent_examples, 1):
            content.extend([
                f"### Example {i}: {example['title']}",
                "",
                example['description'],
                "",
                "```python",
                example['code'].strip(),
                "```",
                ""
            ])
        
        with open(examples_dir / "API_EXAMPLES.md", 'w', encoding='utf-8') as f:
            f.write("\n".join(content))


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate API documentation for Hledac")
    parser.add_argument(
        "--package-path", 
        default="hledac",
        help="Path to the Python package to document"
    )
    parser.add_argument(
        "--output-dir",
        default="docs",
        help="Output directory for documentation"
    )
    
    args = parser.parse_args()
    
    generator = APIDocGenerator(args.package_path)
    generator.generate_markdown_documentation(args.output_dir)
    
    print("API documentation generation completed!")


if __name__ == "__main__":
        main()