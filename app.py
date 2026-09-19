import ipaddress
import os
import re
import socket
from urllib.parse import quote, urljoin, urlparse
from urllib.request import ProxyHandler, Request, build_opener

import yt_dlp
from flask import Flask, Response, jsonify, request, stream_with_context

# On Vercel, files in /public are served by the CDN. Locally Flask serves them.
app = Flask(__name__, static_folder="public", static_url_path="")

FID = re.compile(r"^[\w\-.]+$")
CHUNK = 10 * 1024 * 1024  # fetch big files in ranges; some CDNs throttle single huge requests
DIRECT = ("https", "http")
HLS = ("m3u8", "m3u8_native")

# Optional: route all traffic through a proxy (set PROXY_URL in Vercel's environment variables),
# e.g. http://user:pass@host:port. Needed when a site blocks Vercel's datacenter IPs.
PROXY = os.environ.get("PROXY_URL", "").strip()
# Optional: comma-separated YouTube player clients to try, e.g. "android_vr,web_safari".
CLIENTS = [c.strip() for c in os.environ.get("YT_CLIENTS", "").split(",") if c.strip()]
_opener = build_opener(ProxyHandler({"http": PROXY, "https": PROXY})) if PROXY else build_opener()


def open_url(req, timeout):
    return _opener.open(req, timeout=timeout)


def is_public(url):
    """Allow only http(s) URLs that resolve to public internet addresses."""
    try:
        u = urlparse(url)
        if u.scheme not in DIRECT or not u.hostname:
            return False
        return all(ipaddress.ip_address(i[4][0]).is_global for i in socket.getaddrinfo(u.hostname, None))
    except Exception:
        return False


def all_public(urls):
    if not all(urlparse(u).scheme in DIRECT for u in urls):
        return False
    return all(is_public(f"https://{h}") for h in {urlparse(u).hostname for u in urls})


def extract(url):
    opts = {
        "quiet": True, "noplaylist": True, "playlist_items": "1",
        "skip_download": True, "cache_dir": False, "socket_timeout": 15,
    }
    if PROXY:
        opts["proxy"] = PROXY
    if CLIENTS:
        opts["extractor_args"] = {"youtube": {"player_client": CLIENTS}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        d = ydl.extract_info(url, download=False)
    if d and d.get("entries") is not None:  # a playlist page: use its first video
        d = next((e for e in d["entries"] if e), None)
    if not d:
        raise ValueError("nothing found")
    return d


def kind(f):
    v, a = f.get("vcodec"), f.get("acodec")
    if v == "none":
        return "audio"
    return "video" if a == "none" else "both"


def fetch(url, headers):
    with open_url(Request(url, headers=headers), 20) as r:
        return r.read().decode("utf-8", "replace").splitlines()


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.post("/api/info")
def info():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not is_public(url):
        return jsonify(error="Please paste a valid public http(s) link."), 400
    try:
        d = extract(url)
    except Exception as e:
        msg = str(e)
        if "not a bot" in msg or "Sign in" in msg:
            return jsonify(error="This site blocked the server's request. Try again later or run the app on your own computer."), 502
        return jsonify(error="Couldn't find a downloadable video at that link. The site may be unsupported, protected (DRM), or need a login."), 400

    formats = []
    for f in d.get("formats") or []:
        proto = f.get("protocol")
        if proto not in DIRECT + HLS or not f.get("url") or f.get("ext") == "mhtml":
            continue
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        formats.append({
            "id": f["format_id"], "ext": f.get("ext"), "kind": kind(f),
            "hls": proto in HLS, "height": f.get("height"), "fps": f.get("fps"),
            "abr": f.get("abr"), "size": f.get("filesize") or f.get("filesize_approx"),
        })
    return jsonify(
        title=d.get("title"), channel=d.get("uploader"), site=d.get("extractor_key"),
        thumbnail=d.get("thumbnail"), duration=d.get("duration"), formats=formats,
    )


def hls_plan(f):
    """Return (list of segment URLs, output extension) for an unencrypted VOD HLS stream."""
    headers = dict(f.get("http_headers") or {})
    url = f["url"]
    lines = fetch(url, headers)
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):  # master playlist: take best variant
        best, best_bw = None, -1
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"BANDWIDTH=(\d+)", l)
                bw = int(m.group(1)) if m else 0
                nxt = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
                if nxt and bw > best_bw:
                    best, best_bw = nxt, bw
        url = urljoin(url, best)
        lines = fetch(url, headers)
    if not any(l.startswith("#EXT-X-ENDLIST") for l in lines):
        raise ValueError("live stream")
    init, segs = None, []
    for l in lines:
        if l.startswith("#EXT-X-KEY") and "METHOD=NONE" not in l:
            raise ValueError("encrypted")
        if l.startswith("#EXT-X-MAP"):
            m = re.search(r'URI="([^"]+)"', l)
            if m:
                init = urljoin(url, m.group(1))
        elif l and not l.startswith("#"):
            segs.append(urljoin(url, l))
    if not segs:
        raise ValueError("empty playlist")
    return ([init] if init else []) + segs, ("mp4" if init else "ts")


def pipe_hls(urls, headers):
    for u in urls:
        with open_url(Request(u, headers=headers), 30) as r:
            while True:
                block = r.read(64 * 1024)
                if not block:
                    break
                yield block


def pipe(f):
    headers = dict(f.get("http_headers") or {})
    size = f.get("filesize")
    pos = 0
    while True:
        h = dict(headers)
        if size:
            end = min(pos + CHUNK, size) - 1
            h["Range"] = f"bytes={pos}-{end}"
        with open_url(Request(f["url"], headers=h), 30) as r:
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
    if not is_public(url) or not FID.match(fid):
        return jsonify(error="Invalid request."), 400
    try:
        d = extract(url)
    except Exception:
        return jsonify(error="Couldn't read that link."), 400

    f = next((x for x in d.get("formats") or [] if x.get("format_id") == fid and x.get("url")), None)
    proto = f.get("protocol") if f else None
    size = None
    if proto in HLS:
        try:
            parts, ext = hls_plan(f)
        except Exception:
            return jsonify(error="This stream can't be downloaded here (it may be encrypted, live or protected)."), 422
        if not all_public(parts):
            return jsonify(error="Invalid stream."), 400
        body = pipe_hls(parts, dict(f.get("http_headers") or {}))
    elif proto in DIRECT:
        if not is_public(f["url"]):
            return jsonify(error="Invalid stream."), 400
        ext, size, body = f.get("ext") or "mp4", f.get("filesize"), pipe(f)
    else:
        return jsonify(error="That format isn't available."), 404

    sub = {"m4a": "mp4", "ts": "mp2t"}.get(ext, ext)
    mime = f"{'audio' if kind(f) == 'audio' else 'video'}/{sub}"
    name = re.sub(r'[\\/:*?"<>|]', "", d.get("title") or "video").strip()[:80] or "video"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.{ext}"}
    if size:
        headers["Content-Length"] = str(size)
    return Response(stream_with_context(body), mimetype=mime, headers=headers)


if __name__ == "__main__":
    app.run(port=5000)
