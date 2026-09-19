# LinkGrab

Paste a video page link, see every downloadable stream, and save the one you want.
Works with YouTube and most other sites supported by yt-dlp.

## Project layout

    app.py            Flask backend (yt-dlp)
    requirements.txt  Python dependencies
    vercel.json       Vercel function settings
    public/index.html Frontend page

## Run locally (VS Code)

1. File > Open Folder, choose this folder.
2. Terminal > New Terminal.
3. `pip install -r requirements.txt`
4. `python app.py`
5. Open http://127.0.0.1:5000

Stop with Ctrl+C. Restart after editing app.py.

## Deploy to Vercel through GitHub

1. `git init`, `git add .`, `git commit -m "init"`
2. Create an empty GitHub repo, then:
   `git branch -M main`
   `git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git`
   `git push -u origin main`
3. On vercel.com: Add New > Project > import the repo > Deploy.

If the build complains about the `functions` pattern, delete vercel.json and redeploy.

## If a site blocks Vercel (for example YouTube)

Some sites refuse requests from cloud datacenter IPs. In Vercel go to
Project > Settings > Environment Variables and add:

- `PROXY_URL` : a proxy address, e.g. `http://user:pass@host:port`
  Use a residential or ISP proxy from a provider that allows video sites.
  All lookups and downloads are routed through it, so it is billed by data used.
- `YT_CLIENTS` (optional, try this first, it is free) : `android_vr,web_safari`

Then Deployments > Redeploy so the variables take effect.

## Limits on Vercel

- No ffmpeg: no MP3/WAV conversion and no merging of separate video and audio.
- Works for direct MP4/WebM files and unencrypted, non-live HLS streams.
- DRM-protected, encrypted and live streams are not supported.
- Some sites block cloud server IPs, so results can vary. Keep yt-dlp up to date.

Only download videos you own, that are licensed for reuse, or that you have permission to save.
