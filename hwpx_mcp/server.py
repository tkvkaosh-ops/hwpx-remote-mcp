#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HWP MCP Extended Server (pyhwp 기반)
Model Context Protocol 서버를 통해 한글 문서 제어 기능 제공

주요 기능:
- 차트 생성 (hwp_create_chart)
- 수식 생성 (hwp_create_equation)
- 문서 읽기/검색 (hwp_read_document, hwp_search_text)
- 폼 템플릿 (hwp_search_template, hwp_create_from_template)

플랫폼 지원:
- Windows: pywin32 COM automation (HWP 파일 생성/편집 가능)
- macOS/Linux: pyhwp (HWP 파일 읽기) + python-hwpx (HWPX 파일 생성)
"""

import sys
import os
import platform
import logging
from typing import Optional
from pathlib import Path
from . import __version__
from .core.validator import XmlValidator
from .core.xml_parser import SecureXmlParser
from .features.query import HwpxQueryEngine
from .models.owpml import HwpxSection
from .features.smart_edit import HwpxSmartEditor

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("hwp-mcp-extended")

# Platform detection
IS_WINDOWS = platform.system() == "Windows"


def get_windows_controller():
    if not IS_WINDOWS:
        return None
    try:
        from hwpx_mcp.tools.windows_hwp_controller import get_hwp_controller

        ctrl = get_hwp_controller()
        if ctrl and not ctrl.is_hwp_running:
            ctrl.connect()
        return ctrl
    except ImportError:
        return None


def get_default_output_dir() -> Path:
    """Get a writable default output directory."""
    if IS_WINDOWS:
        # Windows: Use Documents folder
        return Path(os.path.expanduser("~/Documents"))
    elif platform.system() == "Darwin":
        # macOS: Use Documents folder
        return Path(os.path.expanduser("~/Documents"))
    else:
        # Linux: Use XDG documents dir or home
        xdg_documents = os.environ.get("XDG_DOCUMENTS_DIR")
        if xdg_documents and Path(xdg_documents).exists():
            return Path(xdg_documents)
        return Path(os.path.expanduser("~/Documents"))


try:
    from mcp.server.fastmcp import FastMCP

    logger.info("FastMCP imported successfully")
except ImportError as e:
    logger.error(f"Failed to import FastMCP: {e}")
    print("Error: Please install MCP with 'pip install mcp fastmcp'", file=sys.stderr)
    sys.exit(1)

try:
    from hwpx_mcp.tools.chart_tools import register_chart_tools
    from hwpx_mcp.tools.equation_tools import register_equation_tools
    from hwpx_mcp.tools.document_tools import register_document_tools
    from hwpx_mcp.tools.template_tools import register_template_tools
    from hwpx_mcp.tools.hwpx_builder import create_hwpx_from_text, HwpxBuilder
    from hwpx_mcp.tools.pyhwp_adapter import (
        PyhwpAdapter,
        HAS_PYHWP,
    )
    from hwpx_mcp.tools.unified_tools import register_unified_tools
    from hwpx_mcp.tools.agent_tools import register_agent_tools
    from hwpx_mcp.tools.remote_documents import (
        build_download_router,
        register_remote_document_tools,
    )

    logger.info("All tool modules imported successfully")
except ImportError as e:
    logger.warning(f"Some modules not available: {e}")

# Import Windows controller only on Windows
_windows_controller = None
if IS_WINDOWS:
    try:
        from hwpx_mcp.tools.windows_hwp_controller import (
            WindowsHwpController,
            get_hwp_controller as _get_windows_hwp_controller,
        )
        from hwpx_mcp.tools.hwp_table_tools import (
            HwpTableTools,
            get_table_tools as _get_table_tools,
        )

        _windows_controller = WindowsHwpController
        _ = _get_windows_hwp_controller, HwpTableTools, _get_table_tools

        logger.info("Windows HWP controller and table tools imported")
    except ImportError as e:
        logger.warning(f"Windows HWP controller not available: {e}")

mcp = FastMCP(
    name="HWP-Extended",
    dependencies=[
        "mcp>=1.0.0",
        "fastmcp>=0.2.0",
        "pyhwp>=0.1a",
        "pandas>=2.0.0",
        "matplotlib>=3.7.0",
        "python-hwpx>=1.9",
    ],
)

_pyhwp_adapter: Optional[PyhwpAdapter] = None


def get_pyhwp_adapter() -> Optional[PyhwpAdapter]:
    """Get or create PyhwpAdapter instance."""
    global _pyhwp_adapter
    if _pyhwp_adapter is None:
        _pyhwp_adapter = PyhwpAdapter()
    return _pyhwp_adapter


def reset_pyhwp_adapter() -> None:
    """Reset global PyhwpAdapter instance."""
    global _pyhwp_adapter
    if _pyhwp_adapter:
        _pyhwp_adapter.close()
        _pyhwp_adapter.cleanup()
    _pyhwp_adapter = None


def initialize_server() -> None:
    """Register all tools with MCP server."""
    logger.info("Initializing HWP Extended MCP Server...")

    try:
        if os.getenv("MCP_PROFILE", "full").lower() == "remote":
            register_remote_document_tools(mcp)
            logger.info("Remote profile registered (3 vendor-neutral HWPX tools)")
            return

        register_chart_tools(mcp, get_pyhwp_adapter)
        register_equation_tools(mcp, get_pyhwp_adapter)
        register_document_tools(mcp, get_pyhwp_adapter)
        register_template_tools(mcp, get_pyhwp_adapter)

        register_unified_tools(mcp)
        register_remote_document_tools(mcp)

        if IS_WINDOWS:
            register_windows_tools(mcp)

        register_agent_tools(mcp)

        logger.info("All tools registered successfully")
    except Exception as e:
        logger.error(f"Error registering tools: {e}")


def register_windows_tools(mcp) -> None:
    """Register Windows-specific tools with MCP server."""
    try:
        from hwpx_mcp.tools.windows_hwp_controller import get_hwp_controller
        from hwpx_mcp.tools.hwp_table_tools import get_table_tools

        def get_windows_table_tools():
            tools = get_table_tools()
            controller = get_windows_controller()
            if controller:
                tools.set_controller(controller)
            return tools

        # Register Windows HWP tools
        @mcp.tool()
        def hwp_windows_connect(
            visible: bool = True, register_security_module: bool = True
        ) -> dict:
            """Connect to HWP application on Windows."""
            controller = get_hwp_controller()
            if controller:
                success = controller.connect(visible, register_security_module)
                return {
                    "success": success,
                    "message": "Connected to HWP"
                    if success
                    else "Failed to connect to HWP",
                }
            return {"success": False, "message": "Windows HWP controller not available"}

        @mcp.tool()
        def hwp_windows_create_document() -> dict:
            """Create new HWP document."""
            controller = get_windows_controller()
            if controller and controller.is_hwp_running:
                success = controller.create_new_document()
                return {
                    "success": success,
                    "message": "Document created"
                    if success
                    else "Failed to create document",
                }
            return {"success": False, "message": "HWP not connected"}

        @mcp.tool()
        def hwp_windows_insert_text(text: str) -> dict:
            """Insert text at cursor position."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_text(text)
                return {
                    "success": success,
                    "message": f"Text inserted: {text[:50]}..."
                    if len(text) > 50
                    else f"Text inserted: {text}",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_windows_save_document(file_path: str) -> dict:
            """Save HWP document."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.save_document(file_path)
                return {
                    "success": success,
                    "message": f"Document saved: {file_path}"
                    if success
                    else "Failed to save document",
                }
            return {"success": False, "message": "No document to save"}

        # Register table tools
        @mcp.tool()
        def hwp_windows_insert_table(rows: int, cols: int) -> dict:
            """Insert table in HWP document."""
            tools = get_windows_table_tools()
            if tools:
                result = tools.insert_table(rows, cols)
                return {"success": not result.startswith("Error"), "message": result}
            return {"success": False, "message": "Table tools not available"}

        @mcp.tool()
        def hwp_windows_create_table_with_data(
            rows: int, cols: int, data: str = None, has_header: bool = False
        ) -> dict:
            """Create table and fill with data (JSON string)."""
            tools = get_windows_table_tools()
            if tools:
                result = tools.create_table_with_data(rows, cols, data, has_header)
                return {"success": not result.startswith("Error"), "message": result}
            return {"success": False, "message": "Table tools not available"}

        @mcp.tool()
        def hwp_windows_fill_table_data(
            data_list: list,
            start_row: int = 1,
            start_col: int = 1,
            has_header: bool = False,
        ) -> dict:
            """Fill existing table with data."""
            tools = get_windows_table_tools()
            if tools:
                # Convert to string list
                str_data = [[str(cell) for cell in row] for row in data_list]
                result = tools.fill_table_with_data(
                    str_data, start_row, start_col, has_header
                )
                return {"success": not result.startswith("Error"), "message": result}
            return {"success": False, "message": "Table tools not available"}

        @mcp.tool()
        def hwp_windows_set_font_style(
            font_name: str = None,
            font_size: int = None,
            bold: bool = False,
            italic: bool = False,
            underline: bool = False,
        ) -> dict:
            """Set font style for current selection or next text."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.set_font_style(
                    font_name, font_size, bold, italic, underline
                )
                return {
                    "success": success,
                    "message": "Font style set"
                    if success
                    else "Failed to set font style",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_windows_fill_column_numbers(
            start: int = 1,
            end: int = 10,
            column: int = 1,
            from_first_cell: bool = True,
        ) -> dict:
            """Fill table column with sequential numbers."""
            tools = get_windows_table_tools()
            if tools:
                result = tools.fill_column_numbers(start, end, column, from_first_cell)
                return {"success": not result.startswith("Error"), "message": result}
            return {"success": False, "message": "Table tools not available"}

        @mcp.tool()
        def hwp_windows_create_complete_document(document_spec: dict) -> dict:
            """Create complete document from specification dict."""
            controller = get_windows_controller()
            if controller:
                return controller.create_complete_document(document_spec)
            return {"success": False, "message": "HWP not connected"}

        @mcp.tool()
        def hwp_windows_batch_operations(operations: list) -> dict:
            """Execute multiple HWP operations in batch."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                return controller.batch_operations(operations)
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_windows_insert_text_with_linebreaks(
            text: str, preserve_linebreaks: bool = True
        ) -> dict:
            """Insert text with optional linebreak preservation."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_text(text, preserve_linebreaks)
                return {
                    "success": success,
                    "message": "Text inserted" if success else "Failed to insert text",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_bookmark(name: str) -> dict:
            """Insert bookmark at cursor position."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_bookmark(name)
                return {
                    "success": success,
                    "message": f"Bookmark '{name}' inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_hyperlink(url: str, display_text: str = None) -> dict:
            """Insert hyperlink at cursor position."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_hyperlink(url, display_text)
                return {
                    "success": success,
                    "message": "Hyperlink inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_table_split_cell(rows: int = 2, cols: int = 1) -> dict:
            """Split current table cell into rows and columns."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.table_split_cell(rows, cols)
                return {
                    "success": success,
                    "message": f"Cell split into {rows}x{cols}"
                    if success
                    else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_table_merge_cells() -> dict:
            """Merge selected table cells."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.table_merge_cells()
                return {
                    "success": success,
                    "message": "Cells merged" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        # ============================================================
        # HWP SDK Extended Tools (from Actions.h, Document.h, etc.)
        # ============================================================

        @mcp.tool()
        def hwp_setup_columns(
            count: int = 1, same_size: bool = True, gap_mm: float = 10.0
        ) -> dict:
            """Configure page columns (MultiColumn)."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.setup_columns(count, same_size, gap_mm)
                return {
                    "success": success,
                    "message": f"Set to {count} columns" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_dutmal(
            main_text: str, sub_text: str, position: str = "top"
        ) -> dict:
            """Insert Dutmal (text with comment above/below)."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_dutmal(main_text, sub_text, position)
                return {
                    "success": success,
                    "message": "Dutmal inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_index_mark(keyword1: str, keyword2: str = "") -> dict:
            """Insert Index Mark."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_index_mark(keyword1, keyword2)
                return {
                    "success": success,
                    "message": f"Index mark '{keyword1}' inserted"
                    if success
                    else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_set_page_hiding(
            hide_header: bool = False,
            hide_footer: bool = False,
            hide_page_num: bool = False,
            hide_border: bool = False,
            hide_background: bool = False,
        ) -> dict:
            """Hide page elements (header, footer, etc.) for current page."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.set_page_hiding(
                    hide_header,
                    hide_footer,
                    hide_page_num,
                    hide_border,
                    hide_background,
                )
                return {
                    "success": success,
                    "message": "Page hiding settings applied" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_auto_number(
            num_type: str = "page", number_format: int = 0, new_number: int = 0
        ) -> dict:
            """Insert Auto Number (e.g., Figure 1, Table 1). Types: page, footnote, endnote, picture, table, equation."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_auto_number(
                    num_type, number_format, new_number
                )
                return {
                    "success": success,
                    "message": f"Auto number ({num_type}) inserted"
                    if success
                    else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_run_action(action_id: str) -> dict:
            """Execute any HWP action by ID (covers 800+ actions).

            Categories: Edit (Copy, Paste), View (ViewZoom), Formatting (CharShapeBold),
            Navigation (MoveDocEnd), Table (TableDeleteRow), etc.
            """
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.run_action(action_id)
                return {
                    "success": success,
                    "message": f"Action '{action_id}' executed"
                    if success
                    else f"Action '{action_id}' failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_page_setup(
            width_mm: float = 210,
            height_mm: float = 297,
            top_margin_mm: float = 20,
            bottom_margin_mm: float = 20,
            left_margin_mm: float = 20,
            right_margin_mm: float = 20,
            orientation: str = "portrait",
            paper_type: str = "a4",
        ) -> dict:
            """Set page layout (margins, size, orientation)."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.page_setup(
                    width_mm,
                    height_mm,
                    top_margin_mm,
                    bottom_margin_mm,
                    left_margin_mm,
                    right_margin_mm,
                    orientation,
                    paper_type,
                )
                return {
                    "success": success,
                    "message": f"Page setup applied ({paper_type}, {orientation})"
                    if success
                    else "Failed to apply page setup",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_page_number(
            position: int = 4,
            number_format: int = 0,
            starting_number: int = 1,
            side_char: str = "",
        ) -> dict:
            """Insert page numbering. Positions: 4=BottomCenter, 2=TopCenter, etc."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_page_number(
                    position, number_format, starting_number, side_char
                )
                return {
                    "success": success,
                    "message": "Page numbering inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_table_format_cell(
            fill_color: int = None,
            border_type: int = 1,
            border_width: int = 1,
        ) -> dict:
            """Format selected table cells (border and fill color)."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.format_cell(fill_color, border_type, border_width)
                return {
                    "success": success,
                    "message": "Cell formatting applied" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_move_to(
            move_id: str = "MoveDocEnd",
            para: int = 0,
            pos: int = 0,
        ) -> dict:
            """Move cursor to position. Options: MoveDocBegin, MoveDocEnd, MoveParaBegin, etc."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.move_to_pos(move_id, para, pos)
                return {
                    "success": success,
                    "message": f"Moved to {move_id}" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_select_range(
            start_para: int,
            start_pos: int,
            end_para: int,
            end_pos: int,
        ) -> dict:
            """Select text range by paragraph and position indices."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.select_range(
                    start_para, start_pos, end_para, end_pos
                )
                return {
                    "success": success,
                    "message": "Range selected" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_header_footer(
            header_or_footer: str = "header",
            content: str = "",
        ) -> dict:
            """Insert header or footer with text content."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_header_footer(header_or_footer, content)
                return {
                    "success": success,
                    "message": f"{header_or_footer.title()} inserted"
                    if success
                    else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_note(
            note_type: str = "footnote",
            content: str = "",
        ) -> dict:
            """Insert footnote or endnote."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_note(note_type, content)
                return {
                    "success": success,
                    "message": f"{note_type.title()} inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_set_edit_mode(mode: str = "edit") -> dict:
            """Set document mode: 'edit', 'readonly', or 'form'."""
            controller = get_windows_controller()
            if controller and controller.is_hwp_running:
                success = controller.set_edit_mode(mode)
                return {
                    "success": success,
                    "message": f"Edit mode set to '{mode}'" if success else "Failed",
                }
            return {"success": False, "message": "HWP not connected"}

        @mcp.tool()
        def hwp_manage_metatags(
            action: str = "list",
            tag_name: str = "",
            tag_value: str = "",
        ) -> dict:
            """Manage document metatags. Actions: 'get', 'set', 'delete', 'list'."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                result = controller.manage_metatags(action, tag_name, tag_value)
                if action in ("get", "list"):
                    return {"success": True, "result": result}
                return {
                    "success": result,
                    "message": f"Metatag {action} completed" if result else "Failed",
                }
            return {"success": False, "message": "No document open"}

        @mcp.tool()
        def hwp_insert_background(
            image_path: str,
            embedded: bool = True,
            fill_option: str = "tile",
        ) -> dict:
            """Insert background image. Fill options: 'tile', 'center', 'stretch', 'fit'."""
            controller = get_windows_controller()
            if controller and controller.is_document_open:
                success = controller.insert_background(
                    image_path, embedded, fill_option
                )
                return {
                    "success": success,
                    "message": "Background inserted" if success else "Failed",
                }
            return {"success": False, "message": "No document open"}

        logger.info(
            "Windows-specific tools registered (including SDK extended features)"
        )

    except ImportError as e:
        logger.warning(f"Windows tools not available: {e}")
    except Exception as e:
        logger.error(f"Error registering Windows tools: {e}")


initialize_server()


@mcp.tool()
def hwp_create_hwpx(text: str, filename: str = "output.hwpx") -> dict:
    """
    Create a native HWPX (OWPML) document from text.
    Works on all platforms (Windows, macOS, Linux) without needing HWP installed.

    Args:
        text: Text content to write
        filename: Output filename (default: output.hwpx)

    Returns:
        dict: Result with file path
    """
    try:
        # Ensure filename ends with .hwpx
        if not filename.lower().endswith(".hwpx"):
            filename += ".hwpx"

        # Use writable output directory
        output_dir = get_default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(output_dir / filename)

        success = create_hwpx_from_text(text, file_path)

        if success:
            return {
                "status": "success",
                "message": f"Created HWPX file at {file_path}",
                "file_path": file_path,
                "size": os.path.getsize(file_path),
            }
        else:
            return {"status": "error", "message": "Failed to build HWPX file"}

    except Exception as e:
        logger.error(f"Error creating HWPX: {e}")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def hwp_create_hwpx_document(
    contents: list[dict], filename: str = "output.hwpx"
) -> dict:
    """
    Create a rich HWPX document with multiple content types.

    Args:
        contents: List of content items.
            Examples:
            [
                {"type": "text", "content": "Hello World", "style": "bold"},
                {"type": "heading", "content": "Chapter 1", "level": 1},
                {"type": "equation", "content": "E = mc^2"},
                {"type": "chart", "content": "...", "data": {...}},
                {"type": "image", "content": "base64_string", "width_mm": 100, "height_mm": 60}
            ]
        filename: Output filename

    Returns:
        dict: Result with file path
    """
    try:
        # Ensure filename ends with .hwpx
        if not filename.lower().endswith(".hwpx"):
            filename += ".hwpx"

        # Use writable output directory
        output_dir = get_default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(output_dir / filename)

        builder = HwpxBuilder()

        for item in contents:
            ctype = item.get("type", "text")
            content = item.get("content", "")

            if ctype == "text":
                style = item.get("style", "default")
                builder.add_text(content, style=style)
            elif ctype == "heading":
                level = item.get("level", 1)
                builder.add_heading(content, level)
            elif ctype == "equation":
                builder.add_equation(content)
            elif ctype == "chart":
                data = item.get("data", {})
                title = content or "Chart"
                chart_type = item.get("chart_type", "bar")
                builder.add_chart(chart_type, data, title)
            elif ctype == "table":
                data = item.get("data", [])
                if data:
                    rows = len(data)
                    cols = max(len(row) for row in data)
                    builder.add_table(rows, cols, data)
            elif ctype == "image":
                # Handle raw image insertion
                try:
                    import base64

                    image_data = base64.b64decode(content)
                    width = item.get("width_mm", 100)
                    height = item.get("height_mm", 60)
                    builder.insert_image(image_data, "image.png", width, height)
                except Exception as e:
                    builder.add_text(f"[Image insertion failed: {str(e)}]")
            else:
                builder.add_text(f"[{ctype}: {content}]")

        success = builder.build(file_path)

        if success:
            return {
                "status": "success",
                "message": f"Created HWPX file at {file_path}",
                "file_path": file_path,
                "size": os.path.getsize(file_path),
            }
        else:
            return {"status": "error", "message": "Failed to build HWPX file"}

    except Exception as e:
        logger.error(f"Error creating HWPX document: {e}")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def hwp_create_text_document(
    contents: list[dict], filename: str = "output.txt", format: str = "text"
) -> dict:
    """Create document as plain text or markdown (cross-platform, no HWP needed)."""
    try:
        if format not in ("text", "markdown", "md"):
            format = "text"

        ext = ".md" if format in ("markdown", "md") else ".txt"
        if not filename.lower().endswith(ext):
            filename = filename.rsplit(".", 1)[0] + ext

        output_dir = get_default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(output_dir / filename)

        lines = []
        for item in contents:
            ctype = item.get("type", "text")
            content = item.get("content", "")

            if ctype == "text":
                lines.append(content)
            elif ctype == "heading":
                level = item.get("level", 1)
                if format in ("markdown", "md"):
                    lines.append(f"{'#' * level} {content}")
                else:
                    lines.append(f"\n{content}\n{'=' * len(content)}")
            elif ctype == "equation":
                if format in ("markdown", "md"):
                    lines.append(f"$$\n{content}\n$$")
                else:
                    lines.append(f"[수식] {content}")
            elif ctype == "list":
                items = item.get("items", [content] if content else [])
                for li in items:
                    lines.append(
                        f"- {li}" if format in ("markdown", "md") else f"  • {li}"
                    )
            else:
                lines.append(content)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return {
            "status": "success",
            "message": f"Created {format} file at {file_path}",
            "file_path": file_path,
            "size": os.path.getsize(file_path),
            "format": format,
        }

    except Exception as e:
        logger.error(f"Error creating text document: {e}")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def hwp_ping() -> dict:
    """
    HWP MCP 서버 상태 확인

    Returns:
        dict: 서버 상태 정보
    """
    pyhwp_status = "available" if HAS_PYHWP else "not_available"

    return {
        "status": "connected",
        "server": "HWP-Extended",
        "version": __version__,
        "platform": platform.system(),
        "is_windows": IS_WINDOWS,
        "pyhwp_status": pyhwp_status,
        "features": [
            "chart_creation",
            "equation_creation",
            "document_reading",
            "document_searching",
            "template_search",
            "template_creation_text",
            "hwpx_creation",  # New feature
            "windows_hwp_control" if IS_WINDOWS else None,
            "table_creation" if IS_WINDOWS else None,
            "font_style_control" if IS_WINDOWS else None,
        ],
        "note": "HWP (binary) creation requires Windows. HWPX creation works on all platforms.",
    }


@mcp.tool()
def hwp_get_capabilities() -> dict:
    """
    HWP MCP 서버가 지원하는 기능 목록 반환

    Returns:
        dict: 지원 기능 목록
    """
    return {
        "server": "HWP-Extended",
        "version": __version__,
        "platform": platform.system(),
        "is_windows": IS_WINDOWS,
        "capabilities": {
            "charts": {
                "types": ["bar", "line", "pie", "area", "scatter", "histogram"],
                "features": ["title", "legend", "labels", "colors"],
                "output": "base64",
                "platform_support": "all",
            },
            "equations": {
                "input_formats": ["latex", "text"],
                "features": ["fractions", "roots", "matrices", "integrals", "greek"],
                "output": "base64",
                "platform_support": "all",
            },
            "documents": {
                "operations": ["read", "search", "info", "paragraphs", "xml"],
                "elements": ["text", "paragraph"],
                "platform_support": "pyhwp" if not IS_WINDOWS else "pywin32",
            },
            "templates": {
                "features": ["search", "info", "create"],
                "field_types": ["text", "date", "number"],
                "output_formats": ["txt", "md"],
                "platform_support": "all",
                "note": "HWP output only on Windows with HWP installed"
                if not IS_WINDOWS
                else None,
            },
            "hwp_creation": {
                "supported": True,  # Changed to True
                "formats": ["hwpx", "hwp"] if IS_WINDOWS else ["hwpx"],
                "note": "HWPX is supported on all platforms. Binary HWP requires Windows.",
            },
            "tables": {
                "supported": IS_WINDOWS,  # Creating complex tables in binary HWP is still Windows specific
                "features": ["insert", "create_with_data", "fill", "navigation"],
                "data_format": "json_2d_array",
                "note": "HWPX table creation supported on all platforms via hwp_create_hwpx_document",
            },
            "font_control": {
                "supported": IS_WINDOWS,
                "features": ["name", "size", "bold", "italic", "underline"],
                "language_support": ["korean", "english", "chinese", "japanese"],
                "note": "Requires Windows with HWP application installed"
                if not IS_WINDOWS
                else None,
            },
        },
    }


@mcp.tool()
def hwp_xml_validate_content(xml_content: str) -> dict:
    """Validate HWPX XML content structure."""
    validator = XmlValidator()
    # Check syntax first
    valid_syntax = validator.validate_syntax(xml_content)
    if not valid_syntax:
        return {"valid": False, "message": "Invalid XML syntax"}

    # Check schema (optional, if xsd loaded)
    valid_schema = validator.validate_schema(xml_content)
    return {
        "valid": valid_schema,
        "message": "Valid XML" if valid_schema else "Schema validation failed",
    }


@mcp.tool()
def hwp_xml_xpath_query(xml_content: str, xpath_query: str) -> dict:
    """Execute XPath query on HWPX XML content."""
    try:
        root = SecureXmlParser.parse_string(xml_content)
        results = HwpxQueryEngine.execute_xpath(root, xpath_query)
        # Convert results to string representation
        serialized = []
        for r in results:
            if hasattr(r, "tag"):  # Element
                serialized.append(SecureXmlParser.to_string(r))
            else:
                serialized.append(str(r))
        return {"success": True, "count": len(results), "results": serialized}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def hwp_xml_parse_section(xml_content: str) -> dict:
    """Parse HWPX Section XML into structured JSON."""
    try:
        section = HwpxSection.from_xml(xml_content)
        return {"success": True, "data": section.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}

        return None
    try:
        from hwpx_mcp.tools.windows_hwp_controller import get_hwp_controller

        ctrl = get_hwp_controller()
        if ctrl and not ctrl.is_hwp_running:
            ctrl.connect()
        return ctrl
    except ImportError:
        return None


@mcp.tool()
def hwp_smart_patch_xml(original_xml: str, modified_xml: str) -> dict:
    """Validate and patch HWPX XML with smart filtering."""
    result = HwpxSmartEditor.validate_edits(original_xml, modified_xml)
    if result["safe"]:
        return {
            "success": True,
            "message": "Edits accepted",
            "patched_xml": modified_xml,
        }
    else:
        return {
            "success": False,
            "error": result["message"],
            "unsafe_actions": result.get("unsafe_actions"),
        }


@mcp.tool()
def hwp_convert_format(
    source_path: str, target_format: str, output_path: str = None
) -> dict:
    """Convert document format (HWP, HWPX, PDF, HTML)."""
    controller = get_windows_controller()
    if not controller:
        return {"success": False, "error": "Conversion requires Windows Controller"}
    if not output_path:
        base = os.path.splitext(source_path)[0]
        ext = target_format.lower()
        output_path = f"{base}.{ext}"
    if not controller.open(source_path):
        return {"success": False, "error": "Failed to open source document"}
    success = controller.save_as_format(output_path, target_format)
    controller.close_document()
    return {"success": success, "output_path": output_path if success else None}


@mcp.tool()
def hwp_export_pdf(source_path: str, output_path: str = None) -> dict:
    """Export HWP/HWPX to PDF."""
    return hwp_convert_format(source_path, "PDF", output_path)


@mcp.tool()
def hwp_export_html(source_path: str, output_path: str = None) -> dict:
    """Export HWP/HWPX to HTML."""
    return hwp_convert_format(source_path, "HTML", output_path)


def main():
    """Entry point for hwpx-mcp command."""
    from .config import get_config

    try:
        config = get_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    logger.info(f"Starting HWP Extended MCP Server | {config}")

    try:
        if config.transport == "stdio":
            mcp.run(transport="stdio")
        elif config.transport in ("http", "streamable-http"):
            from contextlib import asynccontextmanager
            from collections.abc import AsyncIterator

            import uvicorn
            from fastapi import FastAPI
            from mcp.server.transport_security import TransportSecuritySettings

            from .agentic.http_api import build_agent_http_router

            mcp.settings.streamable_http_path = config.path or "/mcp"
            mcp.settings.stateless_http = config.stateless
            mcp.settings.json_response = config.json_response
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
            mcp_app = mcp.streamable_http_app()

            @asynccontextmanager
            async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
                async with mcp.session_manager.run():
                    yield

            app = FastAPI(lifespan=lifespan)
            app.include_router(build_download_router())
            app.include_router(build_agent_http_router(mcp))
            app.mount("/", mcp_app)

            uvicorn.run(app, host=config.host, port=config.port, log_level="info")
        elif config.transport == "sse":
            import uvicorn
            import asyncio

            asyncio.run(mcp.run_sse_async(mount_path=config.path))
        else:
            logger.error(f"Unsupported transport: {config.transport}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error running server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
