import re
from urllib.parse import quote
from urllib.request import Request, urlopen

import yt_dlp
from flask import Flask, Response, jsonify, request, stream_with_context

# On Vercel, files in /public are served by the CDN. Locally Flask serves them.
app = Flask(__name__, static_folder="public", static_url_path="")

YT_URL = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)
FID = re.compile(r"^[\w\-.]+$")
CHUNK = 10 * 1024 * 1024  # YouTube throttles single huge requests, so fetch in ranges


def extract(url):
    opts = {"quiet": True, "noplaylist": True, "skip_download": True, "cache_dir": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def has(codec):
    return codec not in (None, "none")


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.post("/api/info")
def info():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not YT_URL.match(url):
        return jsonify(error="That doesn't look like a YouTube link."), 400
    try:
        d = extract(url)
    except Exception as e:
        msg = str(e)
        if "not a bot" in msg or "Sign in" in msg:
            return jsonify(error="YouTube blocked this server's request. Try again later or run the app on your own computer."), 502
        return jsonify(error="Couldn't read that video. It may be private, removed or region-locked."), 400

    formats = []
    for f in d.get("formats", []):
        if f.get("protocol") not in ("https", "http") or not f.get("url"):
            continue
        if f.get("ext") == "mhtml":
            continue
        v, a = has(f.get("vcodec")), has(f.get("acodec"))
        if not (v or a):
            continue
        formats.append({
            "id": f["format_id"],
            "ext": f.get("ext"),
            "kind": "both" if v and a else "video" if v else "audio",
            "height": f.get("height"),
            "fps": f.get("fps"),
            "abr": f.get("abr"),
            "size": f.get("filesize") or f.get("filesize_approx"),
        })
    return jsonify(
        title=d.get("title"),
        channel=d.get("uploader"),
        thumbnail=d.get("thumbnail"),
        duration=d.get("duration"),
        formats=formats,
    )


def pipe(f):
    headers = dict(f.get("http_headers") or {})
    size = f.get("filesize")
    pos = 0
    while True:
        h = dict(headers)
        if size:
            end = min(pos + CHUNK, size) - 1
            h["Range"] = f"bytes={pos}-{end}"
        with urlopen(Request(f["url"], headers=h), timeout=30) as r:
            while True:
                block = r.read(64 * 1024)
                if not block:
                    break
                yield block
        if not size:
            return
        pos = end + 1
        if pos >= size:
            return


@app.get("/api/download")
def download():
    url = request.args.get("url", "").strip()
    fid = request.args.get("fid", "")
    if not YT_URL.match(url) or not FID.match(fid):
        return jsonify(error="Invalid request."), 400
    try:
        d = extract(url)
    except Exception:
        return jsonify(error="Couldn't read that video."), 400

    f = next((x for x in d.get("formats", []) if x.get("format_id") == fid and x.get("url")), None)
    if not f or f.get("protocol") not in ("https", "http"):
        return jsonify(error="That format isn't available."), 404

    ext = f.get("ext", "mp4")
    kind = "video" if has(f.get("vcodec")) else "audio"
    mime = f"{kind}/{ {'m4a': 'mp4'}.get(ext, ext) }"
    name = re.sub(r'[\\/:*?"<>|]', "", d.get("title") or "video").strip()[:80] or "video"

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.{ext}"}
    if f.get("filesize"):
        headers["Content-Length"] = str(f["filesize"])
    return Response(stream_with_context(pipe(f)), mimetype=mime, headers=headers)


if __name__ == "__main__":
    app.run(port=5000)
