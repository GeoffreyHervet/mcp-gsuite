# Drive tools added locally to mcp-gsuite (upstream only ships Gmail and Calendar).
# Not part of upstream MarkusPfundstein/mcp-gsuite.
import base64
import io
import json
from collections.abc import Sequence
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from . import gauth
from . import toolhandler

FIELDS = "id, name, mimeType, modifiedTime, size, owners(emailAddress), webViewLink, parents"
EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/png",
}


def _service(user_id: str):
    credentials = gauth.get_stored_credentials(user_id=user_id)
    if not credentials:
        raise RuntimeError("No Oauth2 credentials stored")
    return build("drive", "v3", credentials=credentials)


def _user_id(args: dict) -> str:
    user_id = args.get(toolhandler.USER_ID_ARG)
    if not user_id:
        raise RuntimeError(f"Missing required argument: {toolhandler.USER_ID_ARG}")
    return user_id


class SearchDriveFilesToolHandler(toolhandler.ToolHandler):
    def __init__(self):
        super().__init__("search_drive_files")

    def get_tool_description(self) -> Tool:
        return Tool(
            name=self.name,
            description="""Searches files in Google Drive (My Drive and shared drives).
            The query uses Drive search syntax, e.g. "name contains 'facture'", "fullText contains 'devis'",
            "mimeType = 'application/pdf'", "'<folder_id>' in parents", "modifiedTime > '2026-01-01T00:00:00'".
            Trashed files are excluded. Without query, returns the most recently modified files.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "__user_id__": self.get_user_id_arg_schema(),
                    "query": {"type": "string", "description": "Drive search query (optional)"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
                },
                "required": [toolhandler.USER_ID_ARG],
            },
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        q = "trashed = false"
        if args.get("query"):
            q += f" and ({args['query']})"
        res = _service(_user_id(args)).files().list(
            q=q,
            pageSize=args.get("max_results", 50),
            orderBy="modifiedTime desc",
            fields=f"files({FIELDS})",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        return [TextContent(type="text", text=json.dumps(res.get("files", []), indent=2, ensure_ascii=False))]


class GetDriveFileToolHandler(toolhandler.ToolHandler):
    def __init__(self):
        super().__init__("get_drive_file")

    def get_tool_description(self) -> Tool:
        return Tool(
            name=self.name,
            description="""Reads a Google Drive file by ID. Google Docs and Slides are exported as plain text,
            Sheets as CSV (first sheet), drawings as PNG. Other files are returned as text when textual,
            otherwise they must be saved to disk with save_to_disk (e.g. PDF, images, Office files).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "__user_id__": self.get_user_id_arg_schema(),
                    "file_id": {"type": "string", "description": "The Drive file ID (see search_drive_files)"},
                    "save_to_disk": {"type": "string", "description": "Full path to save the file content to disk (optional)"},
                },
                "required": ["file_id", toolhandler.USER_ID_ARG],
            },
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "file_id" not in args:
            raise RuntimeError("Missing required argument: file_id")
        files = _service(_user_id(args)).files()
        meta = files.get(fileId=args["file_id"], fields=FIELDS, supportsAllDrives=True).execute()
        mime = meta["mimeType"]
        if mime in EXPORT_MIME:
            mime = EXPORT_MIME[mime]
            request = files.export_media(fileId=meta["id"], mimeType=mime)
        elif mime.startswith("application/vnd.google-apps."):
            return [TextContent(type="text", text=f"Cannot download {mime} items. Metadata: {json.dumps(meta, ensure_ascii=False)}")]
        else:
            request = files.get_media(fileId=meta["id"], supportsAllDrives=True)

        textual = mime.startswith("text/") or mime in ("application/json", "application/xml")
        if not textual and not args.get("save_to_disk"):
            return [TextContent(type="text", text=f"Binary file ({mime}): pass save_to_disk to download it. Metadata: {json.dumps(meta, ensure_ascii=False)}")]

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = buf.getvalue()

        if args.get("save_to_disk"):
            with open(args["save_to_disk"], "wb") as f:
                f.write(data)
            return [TextContent(type="text", text=f"Saved {meta['name']} ({mime}, {len(data)} bytes) to {args['save_to_disk']}")]
        return [TextContent(type="text", text=f"{json.dumps(meta, ensure_ascii=False)}\n\n{data.decode('utf-8', errors='replace')}")]
