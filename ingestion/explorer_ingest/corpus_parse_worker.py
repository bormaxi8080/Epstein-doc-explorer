"""One resource-bounded parser operation in a disposable process."""

import json
import sys
from pathlib import Path

from .archive_limits import worker_limits


def main():
    request = json.loads(sys.stdin.readline())
    worker_limits(
        request["seconds"],
        request["memory_mb"],
        request["parent_pid"],
        file_bytes=request["output_bytes"],
    )
    path = Path(request["path"])
    if path.stat().st_size > request["input_bytes"]:
        raise ValueError("Source exceeds parser input budget")
    with path.open("rb") as stream:
        header = stream.read(1024)
    from .corpus_formats import expand_worker, non_pdf

    if request["action"] == "expand":
        print(json.dumps(expand_worker(request, path)), flush=True)
        return
    if b"%PDF-" not in header:
        print(json.dumps(non_pdf(request, path, header)))
        return
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        if request["action"] == "inspect":
            result = {"format": "pdf", "pages": len(document), "status": "inspected"}
        else:
            index = request["page"] - 1
            if not 0 <= index < len(document):
                raise ValueError("Page number is outside the document")
            page = document[index]
            try:
                geometry = {
                    "width_points": page.get_width(),
                    "height_points": page.get_height(),
                    "rotation": page.get_rotation(),
                    "bbox": list(page.get_bbox()),
                }
                if request["action"] == "extract":
                    textpage = page.get_textpage()
                    try:
                        data = {
                            "text": textpage.get_text_range(),
                            "geometry": geometry,
                            "extractor": "pdfium-5.15.2",
                        }
                    finally:
                        textpage.close()
                    with Path(request["output"]).open("x", encoding="utf-8") as output:
                        json.dump(data, output, ensure_ascii=False)
                    result = {
                        "format": "pdf",
                        "pages": len(document),
                        "bytes": Path(request["output"]).stat().st_size,
                    }
                elif request["action"] == "render":
                    scale = request["dpi"] / 72
                    if page.get_width() * scale * page.get_height() * scale > request["max_pixels"]:
                        raise ValueError("Rendered page exceeds pixel budget")
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                        try:
                            if image.width * image.height > request["max_pixels"]:
                                raise ValueError("Rendered page exceeds pixel budget")
                            image.save(request["output"], format="JPEG", quality=90)
                            result = {
                                "format": "jpeg",
                                "width": image.width,
                                "height": image.height,
                                "dpi": request["dpi"],
                                "geometry": geometry,
                                "renderer": "pdfium-5.15.2-pillow-12.3.0",
                            }
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                else:
                    raise ValueError("Unknown parser operation")
            finally:
                page.close()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
