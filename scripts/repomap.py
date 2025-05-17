#!/usr/bin/env python3
"""
Repository Mapper - Generate a comprehensive map of a code repository

This script analyzes a codebase and generates multiple markdown reports organized into 
a structured directory format, including:
- Logical component inventory
- Class and function signatures with descriptions
- Interface components
- Dependency maps using Mermaid diagrams
- Code quality standards and architecture patterns

The output is organized into a versioned directory structure that makes it easier to navigate
large codebases with many components.

Usage:
  python repomap.py [options] [path]

Options:
  --exclude-dir DIR    Exclude directory from analysis (can be used multiple times)
  --exclude-file FILE  Exclude file pattern from analysis (can be used multiple times)
  --include-dir DIR    Only include these directories in analysis (can be used multiple times)
  --include-file FILE  Only include these file patterns in analysis (can be used multiple times)
  --out DIR            Output directory for reports (default: ./repomap)
  --max-versions NUM   Maximum number of report versions to keep (default: 5)
  --verbose            Enable verbose (DEBUG) logging

Output Directory Structure:
  ./repomap/                    # Main output directory (configurable with --out)
  ./repomap/v_TIMESTAMP/        # Version directory with timestamp
  ./repomap/v_TIMESTAMP/index.md                   # Main index
  ./repomap/v_TIMESTAMP/overview.md                # Overview with diagrams
  ./repomap/v_TIMESTAMP/architecture_patterns.md   # Architecture patterns
  ./repomap/v_TIMESTAMP/components/                # Individual component reports
  ./repomap/v_TIMESTAMP/interfaces/                # Interface documentation
"""

import os
import sys
import re
import ast
import argparse
import logging
import fnmatch
import datetime
import shutil
import glob
from typing import Dict, List, Set, Tuple, Optional, Any, Union
from dataclasses import dataclass, field
from collections import defaultdict
import importlib.util

# Check if tree-sitter is available, otherwise use AST module
try:
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("repomap")

# Define colors for Mermaid diagrams (high contrast for dark/light themes)
MERMAID_COLORS = {
    "component": "#3498db",  # Blue
    "class": "#2ecc71",      # Green
    "function": "#e74c3c",   # Red
    "interface": "#9b59b6",  # Purple
    "module": "#f39c12",     # Orange
    "dependency": "#95a5a6", # Gray
}


@dataclass
class Function:
    """Represents a function or method in the codebase."""
    name: str
    signature: str
    docstring: str = ""
    is_public: bool = True
    return_type: str = "None"
    parent_class: Optional[str] = None
    file_path: str = ""
    line_number: int = 0
    complexity: int = 0  # Cyclomatic complexity


@dataclass
class Class:
    """Represents a class in the codebase."""
    name: str
    signature: str
    docstring: str = ""
    methods: List[Function] = field(default_factory=list)
    base_classes: List[str] = field(default_factory=list)
    is_interface: bool = False
    file_path: str = ""
    line_number: int = 0


@dataclass
class Module:
    """Represents a Python module in the codebase."""
    name: str
    file_path: str
    docstring: str = ""
    classes: List[Class] = field(default_factory=list)
    functions: List[Function] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)


@dataclass
class Component:
    """Represents a logical component in the codebase."""
    name: str
    description: str = ""
    modules: List[Module] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)


class RepoMapper:
    """Main class for mapping a repository."""
    
    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)
        self.components: Dict[str, Component] = {}
        self.modules: Dict[str, Module] = {}
        self.class_count = 0
        self.function_count = 0
        self.interface_count = 0
        self.component_count = 0
        self.excluded_dirs: List[str] = []
        self.excluded_files: List[str] = []
        self.included_dirs: List[str] = []
        self.included_files: List[str] = []
        
        if TREE_SITTER_AVAILABLE:
            logger.info("Using tree-sitter for code analysis")
            self._setup_tree_sitter()
        else:
            logger.info("Tree-sitter not available, using Python AST module")
    
    def _setup_tree_sitter(self):
        """Set up the tree-sitter parser if available."""
        if not TREE_SITTER_AVAILABLE:
            return
        
        # This is where you would build and load tree-sitter languages
        # For now, we'll just log that it's not fully implemented
        logger.warning("Tree-sitter support is incomplete - falling back to AST")
    
    def should_analyze_file(self, file_path: str) -> bool:
        """Determine if a file should be analyzed based on exclusion/inclusion rules."""
        rel_path = os.path.relpath(file_path, self.root_path)
        
        # Check if file matches any exclusion pattern
        for pattern in self.excluded_files:
            if fnmatch.fnmatch(os.path.basename(file_path), pattern):
                logger.debug(f"Excluding file (pattern match): {rel_path}")
                return False
        
        # Check if file is in an excluded directory
        for excluded_dir in self.excluded_dirs:
            excluded_path = os.path.join(self.root_path, excluded_dir)
            if file_path.startswith(excluded_path):
                logger.debug(f"Excluding file (in excluded dir): {rel_path}")
                return False
        
        # If inclusion dirs specified, file must be in one of them
        if self.included_dirs:
            in_included_dir = False
            for included_dir in self.included_dirs:
                included_path = os.path.join(self.root_path, included_dir)
                if file_path.startswith(included_path):
                    in_included_dir = True
                    break
            
            if not in_included_dir:
                logger.debug(f"Excluding file (not in included dir): {rel_path}")
                return False
        
        # If inclusion files specified, file must match one pattern
        if self.included_files:
            matches_pattern = False
            for pattern in self.included_files:
                if fnmatch.fnmatch(os.path.basename(file_path), pattern):
                    matches_pattern = True
                    break
            
            if not matches_pattern:
                logger.debug(f"Excluding file (doesn't match include pattern): {rel_path}")
                return False
        
        return True
    
    def map_repository(self):
        """Map the entire repository."""
        logger.info(f"Starting repository mapping at: {self.root_path}")
        
        # First pass - collect all Python files
        python_files = []
        for root, dirs, files in os.walk(self.root_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if os.path.join(root, d) not in self.excluded_dirs]
            
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    if self.should_analyze_file(file_path):
                        python_files.append(file_path)
        
        logger.info(f"Found {len(python_files)} Python files to analyze")
        
        # Second pass - analyze each file
        for file_path in python_files:
            self._analyze_file(file_path)
        
        # Third pass - identify components and relationships
        self._identify_components()
        self._analyze_dependencies()
        
        logger.info(f"Repository mapping complete")
        logger.info(f"Stats: {self.component_count} components, {self.class_count} classes, "
                   f"{self.function_count} functions, {self.interface_count} interfaces")
    
    def _analyze_file(self, file_path: str):
        """Analyze a Python file to extract classes, functions, etc."""
        rel_path = os.path.relpath(file_path, self.root_path)
        logger.debug(f"Analyzing file: {rel_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Generate a module name from the file path
            module_name = os.path.splitext(rel_path)[0].replace(os.path.sep, '.')
            
            # Parse the file using AST
            tree = ast.parse(content, filename=file_path)
            
            # Extract module docstring
            module_docstring = ast.get_docstring(tree) or ""
            
            # Create a new module
            module = Module(
                name=module_name,
                file_path=file_path,
                docstring=module_docstring
            )
            
            # Extract imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        module.imports.append(name.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for name in node.names:
                            module.imports.append(f"{node.module}.{name.name}")
            
            # Extract classes and functions
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    class_item = self._process_class(node, file_path)
                    module.classes.append(class_item)
                    self.class_count += 1
                    
                    # Check if this might be an interface
                    if self._is_interface(node):
                        class_item.is_interface = True
                        self.interface_count += 1
                
                elif isinstance(node, ast.FunctionDef):
                    function = self._process_function(node, file_path)
                    module.functions.append(function)
                    self.function_count += 1
            
            # Add module to the collection
            self.modules[module_name] = module
            
        except Exception as e:
            logger.error(f"Error analyzing file {rel_path}: {str(e)}")
    
    def _process_class(self, node: ast.ClassDef, file_path: str) -> Class:
        """Process a class definition node."""
        # Extract base classes
        base_classes = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_classes.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_classes.append(self._get_attribute_name(base))
        
        # Create the class signature
        signature = f"class {node.name}"
        if base_classes:
            signature += f"({', '.join(base_classes)})"
        
        # Create the class
        class_item = Class(
            name=node.name,
            signature=signature,
            docstring=ast.get_docstring(node) or "",
            base_classes=base_classes,
            file_path=file_path,
            line_number=node.lineno
        )
        
        # Process methods
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method = self._process_function(item, file_path, class_item.name)
                class_item.methods.append(method)
                self.function_count += 1
        
        return class_item
    
    def _process_function(self, node: ast.FunctionDef, file_path: str, parent_class: Optional[str] = None) -> Function:
        """Process a function definition node."""
        # Determine if the function is public or private
        is_public = not node.name.startswith('_') or node.name.startswith('__') and node.name.endswith('__')
        
        # Extract return type annotation if present
        return_type = "None"
        if node.returns:
            return_type = self._get_annotation_name(node.returns)
        
        # Build the function signature
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {self._get_annotation_name(arg.annotation)}"
            args.append(arg_str)
        
        # Add default arguments
        defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + node.args.defaults
        for i, default in enumerate(defaults):
            if default:
                if isinstance(default, ast.Constant):
                    args[i] += f" = {default.value}"
                else:
                    args[i] += " = ..."
        
        # Add *args if present
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        
        # Add **kwargs if present
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        
        signature = f"def {node.name}({', '.join(args)})"
        if return_type != "None":
            signature += f" -> {return_type}"
        
        # Create the function
        function = Function(
            name=node.name,
            signature=signature,
            docstring=ast.get_docstring(node) or "",
            is_public=is_public,
            return_type=return_type,
            parent_class=parent_class,
            file_path=file_path,
            line_number=node.lineno,
            complexity=self._calculate_complexity(node)
        )
        
        return function
    
    def _get_annotation_name(self, node: ast.expr) -> str:
        """Get a string representation of a type annotation."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_attribute_name(node)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                base = node.value.id
            else:
                base = self._get_annotation_name(node.value)
            
            if isinstance(node.slice, ast.Index):  # Python < 3.9
                if hasattr(node.slice, 'value'):
                    if isinstance(node.slice.value, ast.Tuple):
                        params = [self._get_annotation_name(elt) for elt in node.slice.value.elts]
                        return f"{base}[{', '.join(params)}]"
                    return f"{base}[{self._get_annotation_name(node.slice.value)}]"
            elif isinstance(node.slice, ast.Tuple):  # Python >= 3.9
                params = [self._get_annotation_name(elt) for elt in node.slice.elts]
                return f"{base}[{', '.join(params)}]"
            else:  # Python >= 3.9
                return f"{base}[{self._get_annotation_name(node.slice)}]"
        elif isinstance(node, ast.Constant):
            return str(node.value)
        return "Any"  # Default to Any if we can't determine
    
    def _get_attribute_name(self, node: ast.Attribute) -> str:
        """Recursively build a dotted attribute name."""
        if isinstance(node.value, ast.Name):
            return f"{node.value.id}.{node.attr}"
        elif isinstance(node.value, ast.Attribute):
            return f"{self._get_attribute_name(node.value)}.{node.attr}"
        return node.attr
    
    def _calculate_complexity(self, node: ast.FunctionDef) -> int:
        """Calculate the cyclomatic complexity of a function."""
        complexity = 1  # Start with 1
        
        # Count branches that increase complexity
        for inner_node in ast.walk(node):
            if isinstance(inner_node, (ast.If, ast.While, ast.For)):
                complexity += 1
            elif isinstance(inner_node, ast.BoolOp) and isinstance(inner_node.op, ast.And):
                complexity += len(inner_node.values) - 1
        
        return complexity
    
    def _is_interface(self, node: ast.ClassDef) -> bool:
        """Determine if a class is likely an interface."""
        # Check for Interface or Abstract in the name
        if "Interface" in node.name or "Abstract" in node.name:
            return True
        
        # Check docstring
        docstring = ast.get_docstring(node) or ""
        if "interface" in docstring.lower():
            return True
        
        # Check if all methods are abstract
        abstract_method_count = 0
        method_count = 0
        
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method_count += 1
                
                # Check for @abstractmethod decorator
                for decorator in item.decorator_list:
                    if isinstance(decorator, ast.Name) and decorator.id == "abstractmethod":
                        abstract_method_count += 1
                        break
                    elif isinstance(decorator, ast.Attribute) and decorator.attr == "abstractmethod":
                        abstract_method_count += 1
                        break
                
                # Check if method body contains only "pass" or "raise NotImplementedError"
                if len(item.body) == 1:
                    if isinstance(item.body[0], ast.Pass):
                        abstract_method_count += 1
                    elif isinstance(item.body[0], ast.Raise):
                        abstract_method_count += 1
        
        # If more than half the methods are abstract, consider it an interface
        return method_count > 0 and abstract_method_count / method_count >= 0.5
    
    def _identify_components(self):
        """Identify logical components based on module organization."""
        # Group modules into components based on package structure
        components = defaultdict(list)
        
        for module_name, module in self.modules.items():
            parts = module_name.split('.')
            
            # Try to identify logical components
            # For now, use the first level package as the component
            if len(parts) > 1:
                component_name = parts[0]
                components[component_name].append(module)
            else:
                # Top-level modules go into a "core" component
                components["core"].append(module)
        
        # Create Component objects
        for component_name, modules in components.items():
            description = f"Component containing {len(modules)} modules"
            
            # Try to infer a better description from module docstrings
            if modules:
                # Use the first module with a non-empty docstring
                for module in modules:
                    if module.docstring.strip():
                        description = module.docstring.split('\n')[0].strip()
                        break
            
            component = Component(
                name=component_name,
                description=description,
                modules=modules
            )
            
            self.components[component_name] = component
        
        self.component_count = len(self.components)
        logger.info(f"Identified {self.component_count} logical components")
    
    def _analyze_dependencies(self):
        """Analyze dependencies between components."""
        # For each component, look at its imports to find dependencies
        for component_name, component in self.components.items():
            dependencies = set()
            
            for module in component.modules:
                for import_name in module.imports:
                    # Check which component this import belongs to
                    import_parts = import_name.split('.')
                    
                    if import_parts[0] in self.components and import_parts[0] != component_name:
                        dependencies.add(import_parts[0])
            
            component.dependencies = list(dependencies)
            logger.debug(f"Component {component_name} has {len(dependencies)} dependencies")
    
    def extract_usage_example(self, function: Function) -> str:
        """Extract usage example from a function's docstring."""
        if not function.docstring:
            return ""
        
        # Look for common patterns that indicate examples
        patterns = [
            r'(?:Example|Usage):\s*```(?:python)?\s*(.*?)```',
            r'(?:Example|Usage):\s*(.*?)(?:\n\n|\Z)',
            r'>>>\s*(.*?)(?:\n\n|\Z)'
        ]
        
        for pattern in patterns:
            matches = re.search(pattern, function.docstring, re.DOTALL)
            if matches:
                return matches.group(1).strip()
        
        return ""
    
    def extract_linting_rules(self) -> Dict[str, str]:
        """Extract linting rules from configuration files."""
        linting_rules = {}
        
        # Check for various linting configuration files
        config_files = {
            "ruff.toml": "Ruff",
            ".flake8": "Flake8",
            "pyproject.toml": "Project Config",
            "setup.cfg": "Setup Config",
            "mypy.ini": "MyPy",
            "pyrightconfig.json": "PyRight"
        }
        
        for filename, tool_name in config_files.items():
            file_path = os.path.join(self.root_path, filename)
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    linting_rules[tool_name] = f"Found configuration for {tool_name}"
                except Exception as e:
                    logger.warning(f"Error reading {filename}: {str(e)}")
        
        return linting_rules
    
    def _cleanup_old_versions(self, output_dir: str, max_versions: int = 5):
        """Retain only the most recent N versions of reports."""
        # List all version directories
        version_dirs = glob.glob(os.path.join(output_dir, "v_*"))
        
        # Sort by creation time (newest first)
        version_dirs.sort(key=lambda x: os.path.getctime(x), reverse=True)
        
        # Remove older versions beyond the limit
        if len(version_dirs) > max_versions:
            for old_dir in version_dirs[max_versions:]:
                logger.info(f"Removing old report version: {old_dir}")
                try:
                    shutil.rmtree(old_dir)
                except Exception as e:
                    logger.warning(f"Failed to remove old version {old_dir}: {str(e)}")
    
    def generate_report(self, output_dir: str = "repomap", max_versions: int = 5):
        """Generate Markdown reports of the repository structure in separate files."""
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Create a new version subdirectory with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        version_dir = os.path.join(output_dir, f"v_{timestamp}")
        os.makedirs(version_dir, exist_ok=True)
        
        # Clean up old versions
        self._cleanup_old_versions(output_dir, max_versions)
        
        logger.info(f"Generating reports in directory: {version_dir}")
        
        # Create separate directories for different report sections
        components_dir = os.path.join(version_dir, "components")
        interfaces_dir = os.path.join(version_dir, "interfaces")
        os.makedirs(components_dir, exist_ok=True)
        os.makedirs(interfaces_dir, exist_ok=True)
        
        # Main index file path
        index_file = os.path.join(version_dir, "index.md")
        overview_file = os.path.join(version_dir, "overview.md")
        arch_patterns_file = os.path.join(version_dir, "architecture_patterns.md")
        
        # Generate the main index file
        self._generate_index_file(index_file, version_dir)
        
        # Generate the overview file
        self._generate_overview_file(overview_file)
        
        # Generate component files
        for name, component in sorted(self.components.items()):
            component_file = os.path.join(components_dir, f"{name}.md")
            self._generate_component_file(component_file, name, component)
        
        # Generate interfaces file
        self._generate_interfaces_file(interfaces_dir)
        
        # Generate architecture patterns file
        self._generate_architecture_patterns_file(arch_patterns_file)
        
        logger.info(f"Report generated successfully in: {version_dir}")
        return version_dir
    
    def _generate_index_file(self, index_file, base_dir):
        """Generate the main index file linking to other report sections."""
        with open(index_file, 'w', encoding='utf-8') as f:
            # Title and overview
            f.write("# Repository Map\n\n")
            f.write(f"Analysis of repository at: `{self.root_path}`\n\n")
            
            f.write("## Repository Summary\n\n")
            f.write(f"- **Components**: {self.component_count}\n")
            f.write(f"- **Classes**: {self.class_count}\n")
            f.write(f"- **Functions**: {self.function_count}\n")
            f.write(f"- **Interfaces**: {self.interface_count}\n\n")
            
            # Navigation links
            f.write("## Navigation\n\n")
            f.write("- [Repository Overview](./overview.md)\n")
            f.write("- [Architecture Patterns](./architecture_patterns.md)\n")
            
            # Components links
            f.write("\n### Components\n\n")
            for name in sorted(self.components.keys()):
                f.write(f"- [{name}](./components/{name}.md)\n")
            
            # Interface links
            interfaces_exist = False
            for component in self.components.values():
                for module in component.modules:
                    for cls in module.classes:
                        if cls.is_interface:
                            interfaces_exist = True
                            break
                    if interfaces_exist:
                        break
                if interfaces_exist:
                    break
            
            if interfaces_exist:
                f.write("\n### Interfaces\n\n")
                f.write("- [All Interfaces](./interfaces/index.md)\n")
            
            f.write("\n## Generation Information\n\n")
            f.write(f"- **Generated on**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- **Generated by**: RepoMapper script\n\n")
    
    def _generate_overview_file(self, overview_file):
        """Generate the overview file with component diagram and linting rules."""
        with open(overview_file, 'w', encoding='utf-8') as f:
            f.write("# Repository Overview\n\n")
            f.write(f"Analysis of repository at: `{self.root_path}`\n\n")
            
            # Code quality and linting
            linting_rules = self.extract_linting_rules()
            if linting_rules:
                f.write("## Code Quality Standards\n\n")
                for tool, description in linting_rules.items():
                    f.write(f"- **{tool}**: {description}\n")
                f.write("\n")
            
            # Component overview diagram
            f.write("## Component Overview Diagram\n\n")
            f.write("```mermaid\n")
            f.write("graph TD\n")
            f.write("    classDef component fill:#3498db,stroke:#333,stroke-width:1px,color:#fff\n")
            
            # Add component nodes
            for name in self.components:
                f.write(f"    {name}[{name}]\n")
                f.write(f"    class {name} component\n")
            
            # Add dependencies with proper styling
            for name, component in self.components.items():
                for dep in component.dependencies:
                    # Use styled links without attempting to apply classes to edges
                    f.write(f"    {name} -.->|uses| {dep}\n")
            
            f.write("```\n\n")
            
            # Components summary
            f.write("## Components Summary\n\n")
            for name, component in sorted(self.components.items()):
                module_count = len(component.modules)
                class_count = sum(len(module.classes) for module in component.modules)
                function_count = sum(len(module.functions) for module in component.modules)
                
                f.write(f"### {name}\n\n")
                f.write(f"{component.description}\n\n")
                f.write(f"- **Modules**: {module_count}\n")
                f.write(f"- **Classes**: {class_count}\n")
                f.write(f"- **Functions**: {function_count}\n")
                f.write(f"- **Dependencies**: {', '.join(component.dependencies) if component.dependencies else 'None'}\n\n")
            
            f.write("[Back to Index](./index.md)\n")
    
    def _generate_component_file(self, component_file, name, component):
        """Generate a detailed file for a component."""
        with open(component_file, 'w', encoding='utf-8') as f:
            f.write(f"# Component: {name}\n\n")
            f.write(f"{component.description}\n\n")
            
            # Modules
            f.write("## Modules\n\n")
            for module in sorted(component.modules, key=lambda m: m.name):
                f.write(f"### {module.name}\n\n")
                if module.docstring:
                    f.write(f"{module.docstring}\n\n")
                else:
                    f.write("No description available.\n\n")
            
            # Dependencies
            f.write("## Dependencies\n\n")
            if component.dependencies:
                for dep in sorted(component.dependencies):
                    f.write(f"- [{dep}](./{dep}.md)\n")
            else:
                f.write("No dependencies.\n")
            
            f.write("\n")
            
            # Classes within this component
            classes_in_component = []
            for module in component.modules:
                for cls in module.classes:
                    classes_in_component.append((module.name, cls))
            
            if classes_in_component:
                f.write("## Classes\n\n")
                for module_name, cls in sorted(classes_in_component, key=lambda x: x[1].name):
                    interface_tag = " (Interface)" if cls.is_interface else ""
                    f.write(f"### {cls.name}{interface_tag}\n\n")
                    f.write(f"*Defined in module: {module_name}*\n\n")
                    f.write(f"```python\n{cls.signature}\n```\n\n")
                    
                    if cls.docstring:
                        f.write(f"{cls.docstring}\n\n")
                    
                    if cls.methods:
                        f.write("**Methods:**\n\n")
                        for method in sorted(cls.methods, key=lambda m: m.name):
                            public_tag = " (public)" if method.is_public else " (private)"
                            f.write(f"- `{method.name}`{public_tag}: ")
                            if method.docstring:
                                f.write(f"{method.docstring.split('.')[0]}.\n")
                            else:
                                f.write("No description available.\n")
                        
                        f.write("\n")
            
            # Functions within this component (not methods)
            functions_in_component = []
            for module in component.modules:
                for func in module.functions:
                    functions_in_component.append((module.name, func))
            
            if functions_in_component:
                f.write("## Functions\n\n")
                for module_name, func in sorted(functions_in_component, key=lambda x: x[1].name):
                    public_tag = " (public)" if func.is_public else " (private)"
                    f.write(f"### {func.name}{public_tag}\n\n")
                    f.write(f"*Defined in module: {module_name}*\n\n")
                    f.write(f"```python\n{func.signature}\n```\n\n")
                    
                    if func.docstring:
                        f.write(f"{func.docstring}\n\n")
                    
                    example = self.extract_usage_example(func)
                    if example:
                        f.write("**Example:**\n\n")
                        f.write(f"```python\n{example}\n```\n\n")
            
            # Detailed class diagram for this component
            if classes_in_component:
                f.write("## Class Diagram\n\n")
                f.write("```mermaid\n")
                f.write("classDiagram\n")
                
                # Add classes
                for module_name, cls in classes_in_component:
                    # Add inheritance relationships
                    if cls.base_classes:
                        for base in cls.base_classes:
                            # Only include inheritance if base class is in our map
                            base_in_component = False
                            for _, other_cls in classes_in_component:
                                if other_cls.name == base:
                                    base_in_component = True
                                    break
                            
                            if base_in_component:
                                f.write(f"    {base} <|-- {cls.name}\n")
                    
                    # Add class with methods - escape curly braces properly in f-string
                    f.write(f"    class {cls.name} {{\n")
                    
                    for method in cls.methods:
                        visibility = "+" if method.is_public else "-"
                        f.write(f"        {visibility} {method.name}()\n")
                    
                    f.write("    }\n")
                
                f.write("```\n\n")
            
            f.write("[Back to Index](../index.md)\n")
    
    def _generate_interfaces_file(self, interfaces_dir):
        """Generate files for interfaces."""
        # Collect all interfaces
        interfaces = []
        for component_name, component in self.components.items():
            for module in component.modules:
                for cls in module.classes:
                    if cls.is_interface:
                        interfaces.append((component_name, module.name, cls))
        
        if not interfaces:
            return
        
        # Create index file for interfaces
        index_file = os.path.join(interfaces_dir, "index.md")
        with open(index_file, 'w', encoding='utf-8') as f:
            f.write("# Interfaces\n\n")
            
            # List of interfaces
            for component_name, module_name, interface in sorted(interfaces, key=lambda x: x[2].name):
                f.write(f"- [{interface.name}](./{interface.name}.md) - *{component_name}.{module_name}*\n")
            
            # Interface implementation diagram
            f.write("\n## Interface Implementations\n\n")
            f.write("```mermaid\n")
            f.write("classDiagram\n")
            
            # Add interfaces
            for component_name, module_name, interface in interfaces:
                # Use double curly braces to escape them in f-strings for Mermaid
                f.write(f"    class {interface.name} {{\n")
                f.write("        <<interface>>\n")
                for method in interface.methods:
                    f.write(f"        + {method.name}()\n")
                f.write("    }\n")
            
            # Add implementations (classes that inherit from interfaces)
            for component_name, component in self.components.items():
                for module in component.modules:
                    for cls in module.classes:
                        if not cls.is_interface:  # Skip interfaces themselves
                            for base in cls.base_classes:
                                # Check if base class is an interface we know about
                                for _, _, interface in interfaces:
                                    if base == interface.name:
                                        f.write(f"    {interface.name} <|.. {cls.name}\n")
                                        break
            
            f.write("```\n\n")
            f.write("[Back to Index](../index.md)\n")
        
        # Create individual files for each interface
        for component_name, module_name, interface in interfaces:
            interface_file = os.path.join(interfaces_dir, f"{interface.name}.md")
            with open(interface_file, 'w', encoding='utf-8') as f:
                f.write(f"# Interface: {interface.name}\n\n")
                f.write(f"*Defined in component: {component_name}, module: {module_name}*\n\n")
                f.write(f"```python\n{interface.signature}\n```\n\n")
                
                if interface.docstring:
                    f.write(f"{interface.docstring}\n\n")
                
                if interface.methods:
                    f.write("## Methods\n\n")
                    for method in sorted(interface.methods, key=lambda m: m.name):
                        f.write(f"### {method.name}\n\n")
                        f.write(f"```python\n{method.signature}\n```\n\n")
                        if method.docstring:
                            f.write(f"{method.docstring}\n\n")
                
                # List implementations
                implementations = []
                for comp_name, comp in self.components.items():
                    for mod in comp.modules:
                        for cls in mod.classes:
                            if not cls.is_interface and interface.name in cls.base_classes:
                                implementations.append((comp_name, mod.name, cls))
                
                if implementations:
                    f.write("## Implementations\n\n")
                    for impl_comp, impl_mod, impl_cls in sorted(implementations, key=lambda x: x[2].name):
                        f.write(f"- [{impl_cls.name}](../components/{impl_comp}.md#{impl_cls.name.lower()}) - *{impl_comp}.{impl_mod}*\n")
                
                f.write("\n[Back to Interfaces](./index.md) | [Back to Index](../index.md)\n")
    
    def _generate_architecture_patterns_file(self, patterns_file):
        """Generate the architecture patterns file."""
        with open(patterns_file, 'w', encoding='utf-8') as f:
            f.write("# Architecture Patterns\n\n")
            
            # Try to infer architecture patterns from the code structure
            patterns_detected = []
            
            # Check for common architectural patterns
            # MVC
            mvc_components = {"model": False, "view": False, "controller": False}
            for name in self.components:
                lower_name = name.lower()
                if "model" in lower_name:
                    mvc_components["model"] = True
                if "view" in lower_name:
                    mvc_components["view"] = True
                if "controller" in lower_name:
                    mvc_components["controller"] = True
            
            if sum(mvc_components.values()) >= 2:
                patterns_detected.append("Model-View-Controller (MVC)")
            
            # Microservices
            if "service" in "".join(self.components.keys()).lower():
                patterns_detected.append("Service-oriented architecture")
            
            # Repository pattern
            if "repository" in "".join(self.components.keys()).lower():
                patterns_detected.append("Repository pattern")
            
            # Factory pattern - detect classes with Factory in name
            factories = []
            for component in self.components.values():
                for module in component.modules:
                    for cls in module.classes:
                        if "Factory" in cls.name:
                            factories.append(cls.name)
            
            if factories:
                patterns_detected.append(f"Factory pattern (found factories: {', '.join(factories)})")
            
            # If we detected patterns, list them
            if patterns_detected:
                f.write("## Detected Patterns\n\n")
                for pattern in patterns_detected:
                    f.write(f"### {pattern}\n\n")
                    # Add some generic description based on the pattern
                    if "MVC" in pattern:
                        f.write("The Model-View-Controller pattern separates an application into three main components:\n")
                        f.write("- **Model**: Data and business logic\n")
                        f.write("- **View**: User interface elements\n")
                        f.write("- **Controller**: Handles user input and updates the model\n\n")
                    elif "Service" in pattern:
                        f.write("Service-oriented architecture organizes the application as a collection of services that communicate with each other.\n")
                        f.write("Each service represents a business capability and can be developed, deployed, and scaled independently.\n\n")
                    elif "Repository" in pattern:
                        f.write("The Repository pattern separates the logic that retrieves data from the underlying storage from the business logic.\n")
                        f.write("It provides a more object-oriented view of the persistence layer.\n\n")
                    elif "Factory" in pattern:
                        f.write("The Factory pattern provides an interface for creating objects without specifying their concrete classes.\n")
                        f.write("It encapsulates object creation logic and provides a way to create objects based on certain conditions.\n\n")
            else:
                f.write("No clear architectural patterns were automatically detected.\n")
                f.write("The codebase might use custom patterns or a hybrid approach.\n\n")
            
            f.write("## Project-Specific Patterns\n\n")
            f.write("This section requires manual input from developers familiar with the codebase.\n")
            f.write("Consider adding information about custom patterns, architecture decisions, and design principles.\n\n")
            
            f.write("[Back to Index](./index.md)\n")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description='Generate a repository map')
    
    parser.add_argument('path', nargs='?', default='.', 
                       help='Path to the repository (default: current directory)')
    
    parser.add_argument('--exclude-dir', action='append', default=[],
                       help='Exclude directory from analysis (can be used multiple times)')
    
    parser.add_argument('--exclude-file', action='append', default=[],
                       help='Exclude file pattern from analysis (can be used multiple times)')
    
    parser.add_argument('--include-dir', action='append', default=[],
                       help='Only include these directories in analysis (can be used multiple times)')
    
    parser.add_argument('--include-file', action='append', default=[],
                       help='Only include these file patterns in analysis (can be used multiple times)')
    
    parser.add_argument('--out', default='repomap',
                       help='Output directory for reports (default: ./repomap)')
    
    parser.add_argument('--max-versions', type=int, default=5,
                       help='Maximum number of report versions to keep (default: 5)')
    
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose (DEBUG) logging')
    
    args = parser.parse_args()
    
    # Set up logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Create the mapper
    mapper = RepoMapper(args.path)
    
    # Set exclusion/inclusion rules
    mapper.excluded_dirs = [os.path.join(mapper.root_path, d) for d in args.exclude_dir]
    mapper.excluded_files = args.exclude_file
    mapper.included_dirs = [os.path.join(mapper.root_path, d) for d in args.include_dir]
    mapper.included_files = args.include_file
    
    # Run the analysis
    mapper.map_repository()
    
    # Generate the report
    output_dir = os.path.abspath(args.out)
    output_path = mapper.generate_report(output_dir, args.max_versions)
    
    # Print summary
    print(f"Repository map generated in: {output_path}")
    print(f"Components: {mapper.component_count}")
    print(f"Classes: {mapper.class_count}")
    print(f"Functions: {mapper.function_count}")
    print(f"Interfaces: {mapper.interface_count}")


if __name__ == "__main__":
    main()