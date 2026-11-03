"""Streaming synthetic inventory generator; URLs are inert .invalid fixtures."""

import json
from pathlib import Path


def generate_inventory(path, count, page_size=1000):
    path = Path(path)
    if path.exists() or count < 1 or page_size < 1:
        raise ValueError("Select a new output file and positive fixture dimensions")
    pages = (count + page_size - 1) // page_size
    metadata = {
        "root_url": "https://fixture.invalid/archive",
        "task_uid": "PW-010",
        "task_version": "synthetic-1",
        "snapshot_started_at": "2026-11-03T00:00:00Z",
        "snapshot_completed_at": "2026-11-03T01:00:00Z",
        "snapshot_id": "synthetic-scale-1",
        "documents_downloaded": False,
    }

    def dump(value, stream):
        stream.write(json.dumps(value, separators=(",", ":")))

    with path.open("x", encoding="utf-8") as stream:
        stream.write('{"schema_version":2,"metadata":')
        dump(metadata, stream)
        stream.write(',"tree":{"id":"root","name":"Synthetic","kind":"directory","children":[')
        for page in range(pages):
            if page:
                stream.write(",")
            stream.write(
                '{"id":"group-%d","name":"Group %d","kind":"directory","children":[' % (page, page)
            )
            for index in range(page * page_size, min((page + 1) * page_size, count)):
                if index != page * page_size:
                    stream.write(",")
                dump(
                    {
                        "id": f"node-{index}",
                        "name": f"Document {index}.txt",
                        "kind": "file",
                        "asset_id": f"asset-{index}",
                    },
                    stream,
                )
            stream.write("]}")
        stream.write(']},"assets":{')
        for index in range(count):
            page = index // page_size
            if index:
                stream.write(",")
            dump(f"asset-{index}", stream)
            stream.write(":")
            url = f"https://fixture.invalid/group-{page}/document-{index}.txt"
            dump(
                {
                    "url": url,
                    "extension": ".txt",
                    "file_kind": "other",
                    "original_name": f"Document {index}.txt",
                    "status": "verified",
                    "size_bytes": 64,
                    "size_source": "content-length",
                    "final_url": url,
                    "mime_type": "text/plain",
                    "occurrences": [
                        {
                            "node_id": f"node-{index}",
                            "page_id": f"page-{page}",
                            "label": "Synthetic",
                            "tag": "a",
                            "attribute": "href",
                            "ordinal": index,
                        }
                    ],
                },
                stream,
            )
        stream.write('},"pages":[')
        for page in range(pages):
            if page:
                stream.write(",")
            dump(
                {
                    "page_id": f"page-{page}",
                    "url": f"https://fixture.invalid/listing/{page}",
                    "state": "done",
                    "checked_at": "2026-11-03T00:00:00Z",
                    "structure_hash": "synthetic",
                    "verified_hash": "synthetic",
                    "error": None,
                },
                stream,
            )
        stream.write('],"validation":')
        dump(
            {
                "schema_valid": True,
                "structure_complete": True,
                "metadata_complete": True,
                "source_stable": True,
                "complete": True,
                "pages_count": pages,
                "unique_files_count": count,
                "file_placements_count": count,
                "known_unique_size_bytes": 64 * count,
                "unknown_sizes_count": 0,
                "issues_count": 0,
            },
            stream,
        )
        stream.write(',"issues":[]}\n')
    return {"assets": count, "pages": pages, "json_bytes": path.stat().st_size}
