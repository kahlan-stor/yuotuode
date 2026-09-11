import os
from flask import Flask, render_template, request, jsonify
import yt_dlp

app = Flask(__name__, template_folder=".")

BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
}

def extract(url, extra=None):
    opts = dict(BASE_OPTS)
    if extra:
        opts.update(extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def clean_format(f):
    return {
        "id": f.get("format_id"),
        "height": f.get("height"),
        "width": f.get("width"),
        "fps": f.get("fps"),
        "ext": f.get("ext"),
        "url": f.get("url"),
        "vcodec": f.get("vcodec"),
        "acodec": f.get("acodec"),
        "filesize": f.get("filesize") or f.get("filesize_approx"),
        "has_video": f.get("vcodec") not in (None, "none"),
        "has_audio": f.get("acodec") not in (None, "none"),
    }

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/search")
def api_search():
    q=request.args.get("q","").strip()
    if not q:
        return jsonify({"items":[]})
    try:
        info=extract("ytsearch20:"+q, {"extract_flat": True})
        items=[]
        for e in info.get("entries",[]):
            if not e: continue
            vid=e.get("id")
            items.append({
                "id":vid,
                "title":e.get("title") or "بدون عنوان",
                "thumbnail":e.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""),
                "channel":e.get("channel") or e.get("uploader") or "",
                "duration":e.get("duration"),
                "url":e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
            })
        return jsonify({"items":items})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.get("/api/video/<video_id>")
def api_video(video_id):
    url=f"https://www.youtube.com/watch?v={video_id}"
    try:
        info=extract(url)
        formats=[]
        seen=set()
        # Prefer progressive formats first, then adaptive video formats.
        candidates=sorted(
            [f for f in info.get("formats",[]) if f.get("url") and f.get("height") and f.get("vcodec") not in (None,"none")],
            key=lambda x: (x.get("height") or 0, 1 if x.get("acodec") not in (None,"none") else 0)
        )
        for f in candidates:
            h=f.get("height")
            # Keep one useful format per resolution, preferring audio+video.
            key=(h, f.get("acodec") not in (None,"none"))
            if key in seen: continue
            seen.add(key)
            formats.append(clean_format(f))
        return jsonify({
            "id":video_id,
            "title":info.get("title"),
            "thumbnail":info.get("thumbnail"),
            "channel":info.get("channel") or info.get("uploader"),
            "duration":info.get("duration"),
            "description":info.get("description") or "",
            "formats":formats,
            "audio_formats":[clean_format(f) for f in info.get("formats",[])
                if f.get("url") and f.get("acodec") not in (None,"none") and f.get("vcodec") in (None,"none")]
        })
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.get("/api/download/<video_id>")
def api_download(video_id):
    kind=request.args.get("type","video")
    quality=request.args.get("quality","")
    url=f"https://www.youtube.com/watch?v={video_id}"
    try:
        if kind=="audio":
            info=extract(url, {"format":"bestaudio/best"})
            return jsonify({
                "title":info.get("title","audio"),
                "url":info.get("url"),
                "ext":info.get("ext") or "webm",
                "kind":"audio"
            })
        fmt="best"
        if quality.isdigit():
            fmt=f"best[height<={int(quality)}][ext=mp4]/best[height<={int(quality)}]/best"
        else:
            fmt="best[ext=mp4]/best"
        info=extract(url, {"format":fmt})
        return jsonify({
            "title":info.get("title","video"),
            "url":info.get("url"),
            "ext":info.get("ext") or "mp4",
            "kind":"video"
        })
    except Exception as e:
        return jsonify({"error":str(e)}),500

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
