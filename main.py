import os
from flask import Flask, render_template, request, jsonify
import yt_dlp

app = Flask(__name__, template_folder=".")

def ydl_info(url, opts=None):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if opts:
        options.update(opts)

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"items": []})

    try:
        info = ydl_info("ytsearch12:" + q, {"extract_flat": True})
        items = []

        for e in info.get("entries", []):
            if not e:
                continue

            video_id = e.get("id")
            items.append({
                "id": video_id,
                "title": e.get("title", "بدون عنوان"),
                "thumbnail": e.get("thumbnail") or (
                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if video_id else ""
                ),
                "channel": e.get("channel") or e.get("uploader") or "",
                "duration": e.get("duration"),
                "url": e.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={video_id}"
                    if video_id else ""
                ),
            })

        return jsonify({"items": items})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/video/<video_id>")
def video(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        info = ydl_info(url, {
            "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
        })

        formats = []
        for f in info.get("formats", []):
            if not f.get("url") or not f.get("height"):
                continue
            if f.get("vcodec") in (None, "none"):
                continue

            formats.append({
                "format_id": f.get("format_id"),
                "quality": f.get("height"),
                "ext": f.get("ext"),
                "url": f.get("url"),
                "has_video": f.get("vcodec") not in (None, "none"),
                "has_audio": f.get("acodec") not in (None, "none"),
            })

        return jsonify({
            "id": video_id,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "stream_url": info.get("url"),
            "formats": formats[-20:],
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/download/<video_id>")
def download(video_id):
    dl_type = request.args.get("type", "video")
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        if dl_type == "audio":
            info = ydl_info(url, {"format": "bestaudio/best"})
            ext = info.get("ext") or "webm"
        else:
            info = ydl_info(url, {
                "format": "best[height<=480][ext=mp4]/best[height<=480]/best"
            })
            ext = info.get("ext") or "mp4"

        return jsonify({
            "title": info.get("title", "video"),
            "download_url": info.get("url"),
            "ext": ext,
            "note": "الرابط المباشر مؤقت ويتم استخراجه عند الطلب."
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
