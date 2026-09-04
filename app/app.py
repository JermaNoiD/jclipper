# app.py
import os
import uuid
import subprocess
import threading
import json
import shutil
import logging
import re
import time
import hashlib
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, session, jsonify, copy_current_request_context, Response
import srt
from urllib.parse import unquote, quote
import boto3

# Define clean_movie_name before filter registration
def clean_movie_name(name):
    name = re.sub(r'\.', ' ', name)
    year_match = re.search(r'(\s|\.)(\d{4})(\s|\.|$)', name)
    if year_match:
        pos = year_match.end(2)
        name = name[:pos]
    name = re.sub(r'\s+', ' ', name).strip()
    return name

TIMESTAMP_RE = re.compile(r'^\d{2}-\d{2}-\d{2}\.\d{3}$')
RES_RE = re.compile(r'^\d+x\d+(p\d+(?:\.\d+)?)?$')

def parse_clip_basename(basename):
    stem = os.path.splitext(basename)[0]
    parts = stem.split('_')
    if len(parts) >= 4 and parts[-3].lower() == 'to' and TIMESTAMP_RE.match(parts[-4]) and TIMESTAMP_RE.match(parts[-2]) and RES_RE.match(parts[-1]):
        time_res = '_'.join(parts[-4:])
        movie_parts = parts[:-4]
    else:
        idx = next((i for i, part in enumerate(parts) if TIMESTAMP_RE.match(part)), None)
        if idx is not None:
            time_res = '_'.join(parts[idx:])
            movie_parts = parts[:idx]
        elif parts and RES_RE.match(parts[-1]):
            time_res = parts[-1]
            movie_parts = parts[:-1]
        else:
            time_res = ''
            movie_parts = parts
    while movie_parts and re.fullmatch(r'\d{4}', movie_parts[-1]):
        movie_parts.pop()
    movie_name = clean_movie_name(' '.join(movie_parts))
    return {'movie': movie_name or 'Video Clip', 'time_res': time_res}

# Quality/codec tags that mark the start of the "technical" portion of a TV
# episode filename. The episode title is everything before the first such tag.
QUALITY_TAG_RE = re.compile(
    r'(?:^|[.\s\-_])'
    r'(?:'
    r'\d{3,4}p'
    r'|4k'
    r'|web[\s.\-]?dl'
    r'|web[\s.\-]?rip'
    r'|web'
    r'|hulu'
    r'|amzn'
    r'|netflix'
    r'|bluray'
    r'|blu[\s.\-]?ray'
    r'|bdremux'
    r'|bdrip'
    r'|hdtv'
    r'|dvd'
    r'|ddp?[\d.]+'
    r'|eac3'
    r'|aac'
    r'|ac3'
    r'|opus'
    r'|h[\s.\-]?26[45]'
    r'|hevc'
    r'|x26[45]'
    r'|av1'
    r')',
    re.IGNORECASE
)

def tv_episode_display_name(video):
    """Return a display name for a TV episode: 'Show - SXXEXX - Title'.

    The show name comes from the show folder; the episode code (S01E02 / 1x18)
    and title are parsed from the filename. Returns None when the video is not
    under a TV shows dir or has no episode code, so callers can fall back to
    the movie behavior.
    """
    if not video:
        return None
    tv_root = next((root for root in TV_SHOWS_DIRS if os.path.abspath(video).startswith(os.path.abspath(root) + os.sep)), None)
    if not tv_root:
        return None
    show_name = os.path.basename(os.path.dirname(os.path.dirname(video)))
    stem = os.path.splitext(os.path.basename(video))[0]
    ep_code_match = re.search(r'(S\d+E\d+|\d+x\d+)', stem, re.IGNORECASE)
    if not ep_code_match:
        return None
    ep_code = ep_code_match.group(0).upper()
    rest = stem[ep_code_match.end():]
    tag_match = QUALITY_TAG_RE.search(rest)
    title = rest[:tag_match.start()] if tag_match else rest
    title = re.sub(r'[.\s\-_]+', ' ', title).strip()
    title = re.sub(r'\s+', ' ', title)
    if title:
        return f"{show_name} - {ep_code} - {title}"
    return f"{show_name} - {ep_code}"

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
app.permanent_session_lifetime = timedelta(minutes=30)
app.logger.setLevel(logging.INFO)

# Register custom Jinja filters
app.jinja_env.filters['basename'] = os.path.basename
app.jinja_env.filters['dirname'] = os.path.dirname
app.jinja_env.filters['unquote'] = unquote
app.jinja_env.filters['splitext'] = os.path.splitext
app.jinja_env.filters['split'] = lambda value, delimiter: value.split(delimiter)
app.jinja_env.filters['regex_match'] = lambda value, pattern: bool(re.match(pattern, value))
app.jinja_env.filters['regex_replace'] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
app.jinja_env.filters['clean_movie_name'] = clean_movie_name
app.jinja_env.filters['parse_clip_name'] = lambda value: parse_clip_basename(value)['movie']

# Environment variables
MOVIES_DIR = os.getenv('MOVIES_DIR', '/movies')
TV_SHOWS_DIRS = [os.path.abspath(p.strip()) for p in os.getenv('TV_SHOWS_DIRS', os.getenv('TV_SHOWS_DIR', '/tv')).split(',') if p.strip()]
OUTPUT_DIR = os.getenv('OUTPUT_DIR', '/output')
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp/jclipper')
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', 'en')
VIDEO_EXTS = os.getenv('VIDEO_EXTENSIONS', 'mp4,mkv,avi,mov,wmv,flv').split(',')
EXCLUDED_FOLDERS = {p.strip().lower() for p in os.getenv('EXCLUDED_FOLDERS', '').split(',') if p.strip()}
PREVIEW_RESOLUTION = os.getenv('PREVIEW_RESOLUTION', '1280x720')
S3_ENDPOINT = os.getenv('S3_ENDPOINT')
S3_REGION = os.getenv('S3_REGION')
S3_BUCKET = os.getenv('S3_BUCKET')
S3_KEY = os.getenv('S3_KEY')
S3_SECRET = os.getenv('S3_SECRET')
FFMPEG_LOG_ENABLED = os.getenv('FFMPEG_LOG_ENABLED', 'true').lower() == 'true'
STARTUP_SCAN_LOG_ENABLED = os.getenv('STARTUP_SCAN_LOG_ENABLED', 'true').lower() == 'true'
S3_ENABLED = all([S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_KEY, S3_SECRET])
AVAILABLE_FORMATS = ['mp4', 'gif', 'mp3', 'wav']

# Browser-playable preview cache (for sources browsers can't play directly, e.g. MKV/HEVC)
PREVIEW_CACHE_DIR = os.getenv('PREVIEW_CACHE_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preview_cache'))
PREVIEW_MAX_CACHE = int(os.getenv('PREVIEW_MAX_CACHE', '20'))
PREVIEW_HEIGHT = int(os.getenv('PREVIEW_HEIGHT', '360'))
# Padding (seconds) added around the selection when rendering a ranged preview,
# so the user can fine-tune the in/out points without re-rendering.
PREVIEW_PAD_SECONDS = int(os.getenv('PREVIEW_PAD_SECONDS', '15'))

def _detect_nvenc():
    """Check whether the ffmpeg on PATH can encode with the GPU (NVENC)."""
    if not os.path.exists('/dev/nvidia0'):
        return False
    try:
        out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=30).stdout
        return 'h264_nvenc' in out
    except Exception:
        return False

# GPU preview encoding (NVENC). Clip encoding always stays on software encoding.
NVENC_AVAILABLE = _detect_nvenc()

# Tracks active ffmpeg Popen objects by temp_job_dir so cancel_encoding can terminate them
active_processes = {}

# Unique ID generated each time the app starts. Used to detect stale browser sessions
# that reference temp dirs deleted by the startup cleanup (e.g. after a Docker restart).
STARTUP_ID = uuid.uuid4().hex

# Log S3 config and logging status for debugging
app.logger.info(f"S3 Config: ENDPOINT={S3_ENDPOINT}, REGION={S3_REGION}, BUCKET={S3_BUCKET}, KEY={'set' if S3_KEY else 'unset'}, SECRET={'set' if S3_SECRET else 'unset'}")
app.logger.info(f"S3 Enabled: {all([S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_KEY, S3_SECRET])}")
app.logger.info(f"FFmpeg Logging Enabled: {FFMPEG_LOG_ENABLED}")
app.logger.info(f"NVENC (GPU preview encoding) available: {NVENC_AVAILABLE}")
app.logger.info(f"Startup Scan Logging Enabled: {STARTUP_SCAN_LOG_ENABLED}")
app.logger.info(f"Excluded Folders: {sorted(EXCLUDED_FOLDERS) if EXCLUDED_FOLDERS else 'none'}")

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
if STARTUP_SCAN_LOG_ENABLED:
    app.logger.info(f"Startup: OUTPUT_DIR {OUTPUT_DIR} exists with contents: {os.listdir(OUTPUT_DIR)}")
    app.logger.info(f"Startup: TEMP_DIR {TEMP_DIR} exists with contents: {os.listdir(TEMP_DIR)}")

# Clear TEMP_DIR on startup
for item in os.listdir(TEMP_DIR):
    item_path = os.path.join(TEMP_DIR, item)
    try:
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)
        if STARTUP_SCAN_LOG_ENABLED:
            app.logger.info(f"Cleared {item} from TEMP_DIR on startup")
    except Exception as e:
        app.logger.error(f"Failed to clear {item} from TEMP_DIR on startup: {str(e)}")

# Cache for video info
video_info_cache = {}

def find_srt_for_video(video_base, srts, root):
    """Find the best matching SRT file for a video, using DEFAULT_LANGUAGE preference."""
    lang_suffix = f'.{DEFAULT_LANGUAGE.lower()}'
    candidate = video_base + lang_suffix + '.srt'
    matching = next((s for s in srts if s.lower() == candidate.lower()), None)
    if matching:
        return os.path.join(root, matching)
    candidate = video_base + '.srt'
    matching = next((s for s in srts if s.lower() == candidate.lower()), None)
    if matching:
        return os.path.join(root, matching)
    matching = next((s for s in srts if lang_suffix in s.lower()), None)
    if matching:
        return os.path.join(root, matching)
    if srts:
        return os.path.join(root, srts[0])
    return None

# Cache movies on startup
movies = []
for root, dirs, files in os.walk(MOVIES_DIR):
    dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_FOLDERS]
    videos = [f for f in files if any(f.lower().endswith('.' + ext) for ext in VIDEO_EXTS)]
    srts = [f for f in files if f.lower().endswith('.srt')]
    for video in videos:
        video_path = os.path.join(root, video)
        video_base = os.path.splitext(video)[0]
        srt_path = find_srt_for_video(video_base, srts, root)
        name = os.path.relpath(video_path, MOVIES_DIR)
        movies.append({'name': name, 'video': video_path, 'srt': srt_path, 'has_srt': bool(srt_path)})

movies = sorted(movies, key=lambda m: os.path.splitext(os.path.basename(m['name']))[0].lower())
if STARTUP_SCAN_LOG_ENABLED:
    for m in movies:
        app.logger.info(f"Movie: {m['name']}, SRT: {m['srt']}, Video: {m['video']}")

# Cache TV shows on startup: TV_SHOWS_DIRS / show / season / episode
tv_shows = []
for root in TV_SHOWS_DIRS:
    if not os.path.isdir(root):
        continue
    root_slug = root.lstrip('/')
    for show_name in sorted(os.listdir(root)):
        if show_name.lower() in EXCLUDED_FOLDERS:
            continue
        show_path = os.path.join(root, show_name)
        if not os.path.isdir(show_path):
            continue
        seasons = []
        for season_name in sorted(os.listdir(show_path)):
            if season_name.lower() in EXCLUDED_FOLDERS:
                continue
            season_path = os.path.join(show_path, season_name)
            if not os.path.isdir(season_path):
                continue
            files = os.listdir(season_path)
            srts = [f for f in files if f.lower().endswith('.srt')]
            episodes = []
            for fname in sorted(f for f in files if any(f.lower().endswith('.' + ext) for ext in VIDEO_EXTS)):
                video_path = os.path.join(season_path, fname)
                video_base = os.path.splitext(fname)[0]
                srt_path = find_srt_for_video(video_base, srts, season_path)
                episodes.append({'name': fname, 'video': video_path, 'srt': srt_path, 'has_srt': bool(srt_path)})
            if episodes:
                seasons.append({'name': season_name, 'episodes': episodes})
        if seasons:
            tv_shows.append({'root': root, 'root_slug': root_slug, 'name': show_name, 'seasons': seasons})
if STARTUP_SCAN_LOG_ENABLED:
    for show in tv_shows:
        app.logger.info(f"TV Show: {show['root']} / {show['name']}, seasons: {[s['name'] for s in show['seasons']]}")

def timedelta_to_srt(t):
    total_seconds = int(t.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int((t.total_seconds() % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def timedelta_from_str(time_str):
    try:
        if time_str is None:
            app.logger.error("time_str is None in timedelta_from_str")
            return timedelta(seconds=0)
        time_str = time_str.split(',')[0] + '.' + time_str.split(',')[1] if ',' in time_str else time_str
        h, m, s = map(float, time_str.split(':'))
        return timedelta(hours=h, minutes=m, seconds=s)
    except Exception as e:
        app.logger.error(f"Error in timedelta_from_str for {time_str}: {str(e)}")
        return timedelta(seconds=0)

def build_ffmpeg_base_cmd(video, start_sec, duration):
    return ['ffmpeg', '-err_detect', 'ignore_err', '-probesize', '100000000', '-analyzeduration', '100000000',
            '-ss', str(start_sec), '-i', video, '-t', str(duration)]

def _clear_job_session():
    for key in ('output', 'format', 'scale_factor', 'temp_job_dir', 'audio_index', 'encoding_pid', 'crf', 'preset', 'audio_bitrate'):
        session.pop(key, None)
    session.modified = True

def get_video_info(video):
    if video not in video_info_cache:
        cmd = ['ffprobe', '-v', 'error', '-show_streams', '-print_format', 'json', video]
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"Running ffprobe for video info: {' '.join(cmd)}")
        try:
            out = subprocess.check_output(cmd, text=True).strip()
            data = json.loads(out)
            streams = data.get('streams', [])
            video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
            audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
            res = [video_stream.get('width', 1920), video_stream.get('height', 1080)] if video_stream else [1920, 1080]
            video_info_cache[video] = {'resolution': res, 'audio_streams': audio_streams}
            app.logger.info(f"Raw video info data for {video}: {json.dumps(streams, indent=2)}")
        except subprocess.CalledProcessError as e:
            app.logger.error(f"ffprobe failed for video info on {video}: {e.output}")
            video_info_cache[video] = {'resolution': [1920, 1080], 'audio_streams': []}
        except Exception as e:
            app.logger.error(f"Error getting video info for {video}: {str(e)}")
            video_info_cache[video] = {'resolution': [1920, 1080], 'audio_streams': []}
    return video_info_cache[video]

def get_resolution(video):
    return get_video_info(video)['resolution']

@app.before_request
def make_session_permanent():
    session.permanent = True
    if session.get('startup_id') != STARTUP_ID:
        app.logger.info(f"Stale session detected (startup_id mismatch). Clearing job session data.")
        _clear_job_session()
        session['startup_id'] = STARTUP_ID

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/movies', methods=['GET'])
def index():
    app.logger.info("Serving movie list page")
    return render_template('index.html', movies=movies)

@app.route('/tv', methods=['GET'])
def tv():
    app.logger.info("Serving TV Clipper page")
    return render_template('tv.html', tv_shows=tv_shows)

@app.route('/tv/<root_slug>/seasons/<show_name>')
def tv_seasons(root_slug, show_name):
    app.logger.info(f"Serving TV seasons page for show: {root_slug}/{show_name}")
    show = next((s for s in tv_shows if s['root_slug'] == root_slug and s['name'].lower() == show_name.lower()), None)
    if not show:
        app.logger.warning(f"Show not found: {root_slug}/{show_name}")
        return redirect(url_for('tv'))
    return render_template('tv_seasons.html', show=show, show_name=show_name, root_slug=root_slug)

@app.route('/tv/<root_slug>/episodes/<show_name>/<season_name>')
def tv_episodes(root_slug, show_name, season_name):
    app.logger.info(f"Serving TV episodes page for show: {root_slug}/{show_name}, season: {season_name}")
    show = next((s for s in tv_shows if s['root_slug'] == root_slug and s['name'].lower() == show_name.lower()), None)
    if not show:
        app.logger.warning(f"Show not found: {root_slug}/{show_name}")
        return redirect(url_for('tv'))
    season = next((s for s in show['seasons'] if s['name'].lower() == season_name.lower()), None)
    if not season:
        app.logger.warning(f"Season not found: {season_name} for show {root_slug}/{show_name}")
        return redirect(url_for('tv_seasons', root_slug=root_slug, show_name=show_name))
    return render_template('tv_episodes.html', show=show, season=season, show_name=show_name, season_name=season_name, root_slug=root_slug)

@app.route('/search_subtitles')
def search_subtitles():
    root_slug = request.args.get('root')
    show_name = request.args.get('show')
    season_name = request.args.get('season')
    back_url = request.args.get('back', url_for('tv'))
    if not root_slug or not show_name:
        app.logger.warning("No root/show provided for search_subtitles, redirecting to tv")
        return redirect(url_for('tv'))
    show = next((s for s in tv_shows if s['root_slug'] == root_slug and s['name'].lower() == show_name.lower()), None)
    if not show:
        app.logger.warning(f"Show not found: {root_slug}/{show_name}")
        return redirect(url_for('tv'))
    if season_name:
        seasons = [s for s in show['seasons'] if s['name'].lower() == season_name.lower()]
    else:
        seasons = show['seasons']
    season_groups = []
    for season in seasons:
        episode_groups = []
        for ep in season['episodes']:
            if not ep.get('has_srt') or not ep.get('srt'):
                continue
            ep_items = []
            try:
                with open(ep['srt'], 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                subs = list(srt.parse(content))
                for sub in subs:
                    ep_items.append({
                        'start_str': timedelta_to_srt(sub.start),
                        'end_str': timedelta_to_srt(sub.end),
                        'content': sub.content.strip(),
                    })
            except Exception as e:
                app.logger.error(f"Error reading SRT for {ep['video']}: {str(e)}")
            if ep_items:
                episode_groups.append({
                    'name': ep['name'],
                    'video': ep['video'],
                    'items': ep_items,
                })
        if episode_groups:
            season_groups.append({
                'name': season['name'],
                'episodes': episode_groups,
            })
    title = f"{show['root_slug']} / {show['name']}"
    if season_name:
        title += f" - {season_name}"
    return render_template('search_subtitles.html', seasons=season_groups, title=title, show_name=show_name, season_name=season_name, root_slug=root_slug, back_url=back_url)

@app.route('/subtitles', methods=['GET', 'POST'])
def subtitles():
    movie = request.args.get('movie')
    if movie:
        session['movie'] = movie
        session.modified = True
        app.logger.info(f"Session after setting movie: {session}")
    else:
        movie = session.get('movie')
    if not movie:
        app.logger.warning("No movie selected, redirecting to index")
        return redirect(url_for('index'))
    app.logger.info(f"Accessing subtitles for movie: {os.path.basename(movie)}")
    srt_path = next((m['srt'] for m in movies if m['video'] == movie), None)
    if srt_path is None:
        for show in tv_shows:
            for season in show['seasons']:
                ep = next((e for e in season['episodes'] if e['video'] == movie), None)
                if ep:
                    srt_path = ep['srt']
                    break
            if srt_path is not None:
                break
    subs = []
    if srt_path:
        app.logger.info(f"Attempting to open SRT file: {srt_path}")
        try:
            with open(srt_path, 'r', encoding='utf-8', errors='ignore') as srt_file:
                content = srt_file.read()
                app.logger.info(f"Read SRT content (first 100 chars): {content[:100]}")
                subs = list(srt.parse(content))
                for sub in subs:
                    sub.start_str = timedelta_to_srt(sub.start)
                    sub.end_str = timedelta_to_srt(sub.end)
                app.logger.info(f"Parsed {len(subs)} subtitles")
        except Exception as e:
            app.logger.error(f"Error reading SRT file {srt_path}: {str(e)}")
    else:
        app.logger.warning(f"No SRT file found for {movie}")
    movie_base = os.path.splitext(os.path.basename(movie))[0]
    pretty_movie_name = tv_episode_display_name(movie) or clean_movie_name(movie_base)
    app.logger.info(f"Passing pretty_movie_name: {pretty_movie_name} to subtitles.html")
    back_url = request.args.get('back', url_for('index'))
    search_query = request.args.get('q', '').strip()
    return render_template('subtitles.html', subs=subs, movie=movie, pretty_movie_name=pretty_movie_name, back_url=back_url, search_query=search_query)

@app.route('/output', methods=['GET', 'POST'])
def output():
    app.logger.info(f"Received request in output: method={request.method}, form={request.form}, args={request.args}")
    start = request.form.get('start', request.args.get('start', session.get('start')))
    end = request.form.get('end', request.args.get('end', session.get('end')))
    video = request.form.get('video', request.args.get('video', session.get('movie')))
    audio_index = request.form.get('audio_index', session.get('audio_index', None))
    app.logger.info(f"Form data: start={start}, end={end}, video={video}, audio_index={audio_index}")
    
    if not start or not end or not video:
        app.logger.warning("Missing start, end, or video in output route, redirecting to index")
        return redirect(url_for('index'))
    
    session['start'] = start
    session['end'] = end
    session['movie'] = video
    session.modified = True
    app.logger.info(f"Output page: Start={start}, End={end}, Video={video}, Audio Index={audio_index}, Session={session}")
    
    video_info = get_video_info(video)
    res = video_info['resolution']
    app.logger.info(f"Using resolution for {video}: {res[0]}x{res[1]}")
    default_format = session.get('format', 'mp4')
    available_formats = AVAILABLE_FORMATS
    source_format = os.path.splitext(video)[1][1:].lower()
    audio_streams = video_info['audio_streams']
    
    # Process audio streams for display
    processed_audio_streams = []
    for idx, s in enumerate(audio_streams):
        tags = s.get('tags', {})
        lang = tags.get('language', 'Unknown').capitalize()
        processed_audio_streams.append({
            'nth': idx,
            'lang': lang,
            'codec': s.get('codec_name', 'Unknown').upper(),
            'channels': s.get('channels', 'Unknown')
        })
    
    has_multiple_audio = len(processed_audio_streams) > 1
    if audio_index is None:
        lang_map = {
            'en': 'eng', 'fr': 'fre', 'es': 'spa', 'de': 'ger', 'it': 'ita',
            'pt': 'por', 'ru': 'rus', 'zh': 'chi', 'ja': 'jpn', 'ko': 'kor'
        }
        default_three = lang_map.get(DEFAULT_LANGUAGE.lower(), 'eng')
        for idx, stream in enumerate(processed_audio_streams):
            if stream['lang'].lower() == default_three:
                audio_index = str(idx)
                break
        else:
            audio_index = '0'
        session['audio_index'] = audio_index
    
    app.logger.info(f"Session in output before check: {session}")
    movie_base = os.path.splitext(os.path.basename(video))[0]
    pretty_movie_name = tv_episode_display_name(video) or clean_movie_name(movie_base)
    app.logger.info(f"Passing pretty_movie_name: {pretty_movie_name} to output.html")
    return render_template('output.html',
                          original_res=res,
                          start=start,
                          end=end,
                          video=video,
                          default_format=default_format,
                          available_formats=available_formats,
                          source_format=source_format,
                          has_multiple_audio=has_multiple_audio,
                          audio_streams=processed_audio_streams,
                          audio_index=audio_index,
                          pretty_movie_name=pretty_movie_name)

def encode_main(output_file, start_sec, duration, scaled_width, scaled_height, format, video, original_width, original_height, scale_factor, temp_job_dir, audio_index, crf=23, preset='veryfast', audio_bitrate='192k'):
    with app.app_context():
        encoding_file = os.path.join(temp_job_dir, 'encoding')
        log_file = os.path.join(temp_job_dir, 'log.txt')
        success_file = os.path.join(temp_job_dir, 'success')
        scale_filter = f'scale={scaled_width}:{scaled_height}:flags=lanczos' if scale_factor != 1.0 else None
        base_cmd = build_ffmpeg_base_cmd(video, start_sec, duration)
        if format == 'mp3':
            main_cmd = base_cmd + [
                '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:a', 'libmp3lame', '-b:a', audio_bitrate, '-ac', '2',
                '-threads', '4', output_file
            ]
        elif format == 'wav':
            main_cmd = base_cmd + [
                '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:a', 'pcm_s16le', '-ac', '2',
                '-threads', '4', output_file
            ]
        elif format == 'gif':
            video_codec = 'gif'
            extra_flags = ['-loop', '0']
            main_cmd = base_cmd + [
                '-map', '0:v:0?', '-map', '-0:a?', '-map', '-0:s?', '-c:v', video_codec
            ]
            if scale_filter:
                main_cmd += ['-vf', scale_filter]
            main_cmd += extra_flags + [output_file]
        else:
            video_codec = 'libx264'
            audio_codec = 'aac'
            main_cmd = base_cmd + [
                '-map', '0:v:0?', '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:v', video_codec, '-crf', str(crf), '-preset', preset, '-c:a', audio_codec, '-b:a', audio_bitrate, '-ac', '2',
                '-threads', '4', '-r', '23.98', '-pix_fmt', 'yuv420p'
            ]
            if scale_filter:
                main_cmd += ['-vf', scale_filter]
            if format == 'mp4':
                main_cmd += ['-movflags', '+faststart']
            main_cmd += [output_file]
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"Running FFmpeg for main: {' '.join(main_cmd)}")
        process = subprocess.Popen(main_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
        active_processes[temp_job_dir] = process
        stdout_lines = []
        stderr_lines = []
        for line in iter(process.stderr.readline, ''):
            stderr_lines.append(line.strip())
            if FFMPEG_LOG_ENABLED:
                app.logger.info(f"FFmpeg main stderr line: {line.strip()}")
        for line in iter(process.stdout.readline, ''):
            stdout_lines.append(line.strip())
            if FFMPEG_LOG_ENABLED:
                app.logger.info(f"FFmpeg main stdout line: {line.strip()}")
        process.wait()
        active_processes.pop(temp_job_dir, None)
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"FFmpeg main completed with returncode: {process.returncode}")
        stdout = '\n'.join(stdout_lines)
        stderr = '\n'.join(stderr_lines)
        ffmpeg_output = f"stdout: {stdout}\nstderr: {stderr}\nreturncode: {process.returncode}"
        if format not in ('mp3', 'wav') and process.returncode == 0 and os.path.exists(output_file):
            probe_cmd = ['ffprobe', '-err_detect', 'ignore_err', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', output_file]
            try:
                res_out = subprocess.check_output(probe_cmd, text=True).strip()
                if FFMPEG_LOG_ENABLED:
                    app.logger.info(f"Output file resolution: {res_out}")
                ffmpeg_output += f"\nOutput resolution: {res_out}"
            except subprocess.CalledProcessError as e:
                if FFMPEG_LOG_ENABLED:
                    app.logger.error(f"Failed to probe output file resolution: {e.output}")
                ffmpeg_output += "\nOutput resolution: Failed to probe"
        with open(log_file, 'w') as f:
            f.write(ffmpeg_output)
        if process.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            open(success_file, 'w').close()
            session['job_dirs'] = session.get('job_dirs', {})
            session['job_dirs'][output_file] = temp_job_dir
            session.modified = True
            if FFMPEG_LOG_ENABLED:
                app.logger.info(f"Main output file: {output_file}, size: {os.path.getsize(output_file)}, temp_job_dir: {temp_job_dir}")
        else:
            if FFMPEG_LOG_ENABLED:
                app.logger.error(f"Main encoding failed or file missing: returncode={process.returncode}")
        # Remove encoding sentinel only after success/failure sentinel is written,
        # so /status never sees a gap where neither file exists.
        try:
            os.remove(encoding_file)
        except Exception:
            pass

@app.route('/generate', methods=['POST'])
def generate():
    start_str = request.form.get('start')
    end_str = request.form.get('end')
    video = request.form.get('video')
    format = request.form.get('format', 'mp4')
    scale_factor = float(request.form.get('scale_factor', 1.0))
    audio_index = request.form.get('audio_index', '0')
    crf = int(request.form.get('crf', 23))
    preset = request.form.get('preset', 'veryfast')
    audio_bitrate = request.form.get('audio_bitrate', '192k')
    # Remove direct resolution parsing from form; calculate from scale_factor
    # res_str = request.form.get('resolution', '1920x1080')
    # scaled_width, scaled_height = map(int, res_str.split('x'))
    app.logger.info(f"Generate request: start={start_str}, end={end_str}, video={video}, format={format}, scale_factor={scale_factor}, audio_index={audio_index}, crf={crf}, preset={preset}, audio_bitrate={audio_bitrate}")
    if not all([start_str, end_str, video]):
        app.logger.warning(f"Missing required params in generate: start={start_str}, end={end_str}, video={video}")
        return jsonify({'error': 'Missing required parameters'}), 400
    try:
        start_td = timedelta_from_str(start_str)
        end_td = timedelta_from_str(end_str)
        start_sec = max(0, start_td.total_seconds())
        duration = end_td.total_seconds() - start_sec
        app.logger.info(f"Calculated start_sec: {start_sec}, duration: {duration}")
        if duration <= 0:
            app.logger.error("Calculated duration is zero or negative, falling back to 10 seconds")
            duration = 10.0
    except Exception as e:
        app.logger.error(f"Error parsing timestamps: {str(e)} - Falling back to 10 seconds")
        start_sec = 0
        duration = 10.0
    tv_root = next((root for root in TV_SHOWS_DIRS if os.path.abspath(video).startswith(os.path.abspath(root) + os.sep)), None)
    if tv_root:
        show_name = os.path.basename(os.path.dirname(os.path.dirname(video)))
        episode_stem = os.path.splitext(os.path.basename(video))[0]
        # Extract just the season/episode code (S01E05 or 1x02) for a clean output name.
        ep_code_match = re.search(r'(S\d+E\d+|\d+x\d+)', episode_stem, re.IGNORECASE)
        if ep_code_match:
            ep_code = ep_code_match.group(0).upper()
            movie_name = f"{show_name}_-_{ep_code}".replace(' ', '_').replace('.', '_')
        else:
            # No episode code found: fall back to show name only
            movie_name = show_name.replace(' ', '_').replace('.', '_')
    else:
        movie_name = os.path.basename(os.path.dirname(video)).replace(' ', '_')
    start_time = start_str.replace(':', '-').replace(',', '.')
    end_time = end_str.replace(':', '-').replace(',', '.')
    res = get_resolution(video)
    original_width, original_height = res
    # Calculate scaled dimensions using scale_factor
    scaled_width = int(original_width * scale_factor)
    scaled_height = int(original_height * scale_factor)
    # Ensure even dimensions for FFmpeg
    scaled_width = scaled_width if scaled_width % 2 == 0 else scaled_width - 1
    scaled_height = scaled_height if scaled_height % 2 == 0 else scaled_height - 1
    app.logger.info(f"Calculated scaled resolution: {scaled_width}x{scaled_height}")
    safe_filename = f"{movie_name}_{start_time}_to_{end_time}_{scaled_width}x{scaled_height}.{format}"
    output_file = os.path.join(OUTPUT_DIR, safe_filename)
    
    # Create per-job temp dir
    job_id = uuid.uuid4().hex
    temp_job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(temp_job_dir, exist_ok=True)
    
    # Store in session before starting encode
    session['start'] = start_str
    session['end'] = end_str
    session['movie'] = video
    session['output'] = output_file
    session['format'] = format
    session['scale_factor'] = scale_factor
    session['temp_job_dir'] = temp_job_dir
    session['audio_index'] = audio_index
    session['crf'] = crf
    session['preset'] = preset
    session['audio_bitrate'] = audio_bitrate
    session.modified = True
    app.logger.info(f"Preserved session in generate: {session}")

    # Create the encoding sentinel before the thread starts so /status immediately
    # sees 'encoding' on the first poll (avoids a race where the thread hasn't
    # created the file yet and /status incorrectly returns 'failure').
    open(os.path.join(temp_job_dir, 'encoding'), 'w').close()

    # Start main encode in background; output page polls /status until complete
    threading.Thread(target=copy_current_request_context(encode_main), args=(output_file, start_sec, duration, scaled_width, scaled_height, format, video, original_width, original_height, scale_factor, temp_job_dir, audio_index, crf, preset, audio_bitrate), daemon=True).start()
    return jsonify({'status': 'encoding'})

@app.route('/preview', methods=['GET'])
def preview():
    app.logger.info(f"Entering preview route, session: {session}")
    history_file = request.args.get('history_file')
    from_history = bool(history_file)
    if from_history:
        output = history_file
        format = os.path.splitext(output)[1][1:].lower()
        main_status = 'success'
        encoding_done = True
        main_ffmpeg_output = ''
        preview = output  # Use the file directly
        # Extract movie_name from basename
        basename = os.path.basename(output)
        parts = basename.split('_')
        movie_parts = []
        for part in parts:
            if re.match(r'\d{2}-\d{2}-\d{2}\.\d{3}', part):
                break
            movie_parts.append(part.replace('_', ' '))
        movie_name = ' '.join(movie_parts) if movie_parts else 'Video Clip'
        app.logger.info(f"Parsed movie_name: {movie_name} from basename: {basename}")
        # Set session for download/share
        session['output'] = output
        session['format'] = format
        session.modified = True
    else:
        output = session.get('output')
        format = session.get('format')
        video = session.get('movie')
        if not output:
            app.logger.warning("No output path in session, redirecting to index")
            return redirect(url_for('index'))
        main_ffmpeg_output = ''
        temp_job_dir = session.get('temp_job_dir')
        if temp_job_dir:
            success_file = os.path.join(temp_job_dir, 'success')
            log_file = os.path.join(temp_job_dir, 'log.txt')
            main_status = 'success' if os.path.exists(success_file) else 'failure'
            encoding_done = True
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    main_ffmpeg_output = f.read()
        else:
            main_status = 'success' if os.path.exists(output) else 'failure'
            encoding_done = True
        app.logger.info(f"output: {output}, exists: {os.path.exists(output)}, main_status: {main_status}")
        movie_name = tv_episode_display_name(video) or (os.path.splitext(os.path.basename(video))[0] if video else 'Video Clip')

    app.logger.info(f"Preview context: output={output}, encoding_done={encoding_done}, main_status={main_status}, from_history={from_history}")
    s3_enabled = S3_ENABLED
    return render_template('preview.html', output=output, format=format, encoding_done=encoding_done, main_status=main_status, main_ffmpeg_output=main_ffmpeg_output, s3_enabled=s3_enabled, movie_name=movie_name, from_history=from_history)

@app.route('/status')
def get_status():
    temp_job_dir = session.get('temp_job_dir')
    if not temp_job_dir:
        return jsonify({'status': 'error', 'message': 'No job directory'})
    
    success_path = os.path.join(temp_job_dir, 'success')
    encoding_path = os.path.join(temp_job_dir, 'encoding')
    log_path = os.path.join(temp_job_dir, 'log.txt')

    if os.path.exists(success_path):
        status = 'success'
    elif os.path.exists(encoding_path):
        status = 'encoding'
    else:
        status = 'failure'
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            ffmpeg_output = f.read()
    except Exception:
        ffmpeg_output = 'No log available'
    
    return jsonify({'status': status, 'ffmpeg_output': ffmpeg_output})

@app.route('/download')
def download():
    app.logger.info(f"Entering download route, session: {session}")
    output = session.get('output')
    if not output or not os.path.exists(output):
        app.logger.warning(f"No output file to download or file not found: {output}, redirecting to index")
        return redirect(url_for('index'))
    try:
        app.logger.info(f"Downloading file: {output}")
        response = send_file(output, as_attachment=True, download_name=os.path.basename(output))
        response.headers['Content-Disposition'] = f'attachment; filename="{os.path.basename(output)}"'
        _clear_job_session()
        return response
    except Exception as e:
        app.logger.error(f"Failed to download file {output}: {str(e)}")
        return redirect(url_for('index'))

@app.route('/upload_s3')
def upload_s3():
    file_param = request.args.get('file')
    if file_param and file_param.startswith(OUTPUT_DIR) and os.path.isfile(file_param):
        output = file_param
        format = os.path.splitext(output)[1][1:].lower() or 'mp4'
    else:
        output = session.get('output')
        format = session.get('format', 'mp4')
    if not output or not os.path.exists(output):
        return jsonify({'success': False, 'message': 'No file to upload'})
    mime_type = 'video/mp4' if format == 'mp4' else 'image/gif' if format == 'gif' else 'audio/mpeg' if format == 'mp3' else 'audio/wav' if format == 'wav' else 'application/octet-stream'
    link_format = os.getenv('S3_LINK_FORMAT', 'presigned').lower()
    try:
        s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name=S3_REGION)
        base_filename = os.path.splitext(os.path.basename(output))[0]
        parts = base_filename.split('_')
        movie_parts = []
        time_res_parts = []
        found_timestamp = False
        for part in parts:
            if '-' in part and part.replace('.', '').replace('-', '').isdigit():
                found_timestamp = True
            if found_timestamp:
                time_res_parts.append(part)
            else:
                movie_parts.append(part)
        movie_title = '_'.join(part for part in movie_parts if part).replace('(', '_').replace(')', '_').replace(' ', '_').replace(',', '_').strip('_')
        time_res = '_'.join(time_res_parts)
        movie_folder = f"{movie_title}/"
        video_filename = f"video_{time_res}.{format}"
        video_key = f"{movie_folder}{video_filename}"
        s3.upload_file(output, S3_BUCKET, video_key, ExtraArgs={'ContentType': mime_type})
        
        if link_format == 'basic':
            video_url = f"{S3_ENDPOINT}/{S3_BUCKET}/{video_key}"
        else:
            video_url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': video_key}, ExpiresIn=604800)
        
        video_response = s3.head_object(Bucket=S3_BUCKET, Key=video_key)
        app.logger.info(f"S3 upload successful, Video URL: {video_url}, Content-Type: {video_response.get('ContentType', 'N/A')}, Link Format: {link_format}")
        
        return jsonify({'success': True, 'url': video_url})
    except Exception as e:
        app.logger.error(f"S3 upload failed: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/cancel_encoding')
def cancel_encoding():
    app.logger.info("Entering cancel_encoding route")
    temp_job_dir = session.get('temp_job_dir')
    proc = active_processes.pop(temp_job_dir, None) if temp_job_dir else None
    if proc:
        try:
            proc.terminate()
            app.logger.info(f"Terminated FFmpeg process for job: {temp_job_dir}")
        except Exception as e:
            app.logger.error(f"Failed to terminate FFmpeg process: {str(e)}")
    
    preview = session.get('preview')
    if preview and os.path.exists(preview):
        try:
            os.remove(preview)
            app.logger.info(f"Removed preview file on cancel: {preview}")
        except Exception as e:
            app.logger.error(f"Failed to remove preview file on cancel {preview}: {str(e)}")
    
    output = session.get('output')
    if output and os.path.exists(output):
        try:
            os.remove(output)
            app.logger.info(f"Removed output file on cancel: {output}")
        except Exception as e:
            app.logger.error(f"Failed to remove output file on cancel {output}: {str(e)}")
    
    temp_job_dir = session.get('temp_job_dir')
    if temp_job_dir and os.path.exists(temp_job_dir):
        try:
            shutil.rmtree(temp_job_dir)
            app.logger.info(f"Removed temp job dir on cancel: {temp_job_dir}")
        except Exception as e:
            app.logger.error(f"Failed to remove temp job dir on cancel {temp_job_dir}: {str(e)}")
    
    if output:
        session['job_dirs'] = session.get('job_dirs', {})
        session['job_dirs'].pop(output, None)
    
    movie = session.get('movie')
    start = session.get('start')
    end = session.get('end')
    
    _clear_job_session()
    
    next_page = request.args.get('next', 'index')
    if next_page == 'output' and movie and start and end:
        return redirect(url_for('output', video=movie, start=start, end=end))
    return redirect(url_for(next_page))

@app.route('/resolution')
def resolution():
    video = session.get('movie')
    scale = float(request.args.get('scale', 1.0))
    res = get_resolution(video)
    w, h = int(res[0] * scale), int(res[1] * scale)
    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1
    return jsonify({'scaled': f'{w}x{h}'})

@app.route('/history')
def history():
    app.logger.info("Serving history page")
    output_files = [f for f in os.listdir(OUTPUT_DIR) if os.path.isfile(os.path.join(OUTPUT_DIR, f)) and not f.endswith(('.log', '.success', '.encoding'))]
    full_paths = [os.path.join(OUTPUT_DIR, f) for f in output_files]
    file_data = [(full_path, os.path.basename(full_path)) for full_path in full_paths]
    app.logger.info(f"Found output files: {[basename for _, basename in file_data]}")
    for _, basename in file_data:
        app.logger.info(f"Parsing basename: {basename}, parts: {basename.split('_')}")
    s3_enabled = S3_ENABLED
    return render_template('history.html', file_data=file_data, s3_enabled=s3_enabled)

@app.route('/delete', methods=['POST'])
def delete():
    file_path = request.form.get('file_path')
    app.logger.info(f"Attempting to delete file: {file_path}")
    if file_path and file_path.startswith(OUTPUT_DIR):
        job_dirs = session.get('job_dirs', {})
        temp_job_dir = job_dirs.get(file_path)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                app.logger.info(f"Successfully deleted file: {file_path}")
            except Exception as e:
                app.logger.error(f"Failed to delete file {file_path}: {str(e)}")
                return jsonify({'success': False, 'message': 'Deletion failed'})
        if temp_job_dir and os.path.exists(temp_job_dir):
            try:
                shutil.rmtree(temp_job_dir)
                app.logger.info(f"Removed temp job dir for deleted file: {temp_job_dir}")
            except Exception as e:
                app.logger.error(f"Failed to remove temp job dir {temp_job_dir}: {str(e)}")
        job_dirs.pop(file_path, None)
        session['job_dirs'] = job_dirs
        session.modified = True
        return jsonify({'success': True})
    else:
        app.logger.warning(f"Invalid or non-existent file path: {file_path}")
        return jsonify({'success': False, 'message': 'Invalid file path'})

@app.route('/clear_all', methods=['POST'])
def clear_all():
    app.logger.info("Clearing all clips")
    success = True
    for f in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, f)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                app.logger.info(f"Successfully deleted {file_path}")
            except Exception as e:
                app.logger.error(f"Failed to delete {file_path}: {str(e)}")
                success = False
    try:
        shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)
        app.logger.info("Cleared TEMP_DIR")
    except Exception as e:
        app.logger.error(f"Failed to clear TEMP_DIR: {str(e)}")
        success = False
    session['job_dirs'] = {}
    session.modified = True
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'Some files or directories failed to delete'})

# ===== Browser-playable preview generation (for sources like MKV/HEVC) =====
_preview_lock = threading.Lock()
_preview_status = {}  # cache_key -> {'state': 'generating'|'ready'|'error', 'error': str}
_preview_progress = {}  # cache_key -> {'pct': 0-100, 'encoder': 'gpu'|'cpu'|'copy'|None} while generating

def _preview_path(key):
    return os.path.join(PREVIEW_CACHE_DIR, key + '.mp4')

def _preview_cache_key(source_path, start=None, duration=None):
    st = os.stat(source_path)
    raw = f"{source_path}|{st.st_size}|{int(st.st_mtime)}"
    if start is not None and duration is not None:
        raw += f"|{start:.3f}|{duration:.3f}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def _probe_codec(path, stream_type):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', stream_type + ':0',
           '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', path]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=30).strip()
        return out.split('\n')[0].strip().lower() if out else ''
    except Exception as e:
        app.logger.error(f"ffprobe codec probe failed for {path} ({stream_type}): {e}")
        return ''

def _probe_duration(path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', path]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=30).strip()
        return float(out.split('\n')[0])
    except Exception as e:
        app.logger.error(f"ffprobe duration probe failed for {path}: {e}")
        return None

def _run_ffmpeg_with_progress(cmd, key, duration, encoder=None):
    """Run an ffmpeg command (with -progress pipe:1) and stream progress to _preview_progress[key] (0-100).

    Progress is read from stdout (the -progress stream, which ffmpeg flushes live). stderr is
    drained on a background thread (for error reporting) so a full stderr pipe can't deadlock us."""
    with _preview_lock:
        _preview_progress[key] = {'pct': 0, 'encoder': encoder}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err_chunks = []
    def _drain_stderr():
        try:
            for line in iter(proc.stderr.readline, b''):
                err_chunks.append(line)
        finally:
            proc.stderr.close()
    drain_thread = threading.Thread(target=_drain_stderr, daemon=True)
    drain_thread.start()
    time_re = re.compile(rb'out_time=(\d+):(\d+):(\d+(?:\.\d+)?)')
    try:
        for raw in iter(proc.stdout.readline, b''):
            m = time_re.search(raw)
            if m and duration and duration > 0:
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                with _preview_lock:
                    _preview_progress[key] = {'pct': min(100, int(t / duration * 100)), 'encoder': encoder}
    finally:
        proc.stdout.close()
    drain_thread.join(timeout=5)
    rc = proc.wait()
    if rc != 0:
        err = b''.join(err_chunks)[-500:].decode('utf-8', 'replace')
        raise RuntimeError(f"ffmpeg preview generation failed: {err}")

def _generate_preview(source, dest, start=None, duration=None, key=None):
    """Produce a browser-playable MP4 at dest. Stream-copy when possible, else transcode.

    When start/duration are given, only that range is rendered (fast input seeking),
    so large sources don't require a full-movie transcode just to preview a clip.
    Transcoding prefers the GPU (NVENC) when available and falls back to libx264."""
    vcodec = _probe_codec(source, 'v')
    acodec = _probe_codec(source, 'a')
    video_copy = (vcodec == 'h264')
    audio_copy = acodec in ('aac', 'mp3')
    if start is not None and duration is not None and duration > 0:
        input_args = ['-ss', str(start), '-i', source, '-t', str(duration)]
    else:
        input_args = ['-i', source]
        if duration is None:
            duration = _probe_duration(source)
    color_opts = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709']
    audio_opts = ['-c:a', 'aac', '-b:a', '128k']
    mp4_opts = ['-f', 'mp4', '-movflags', '+faststart']
    if video_copy and audio_copy:
        candidates = [('stream-copy', ['ffmpeg', '-y'] + input_args + ['-c', 'copy'] + mp4_opts)]
    elif video_copy:
        candidates = [('copy-video/aac-audio', ['ffmpeg', '-y'] + input_args + ['-c:v', 'copy'] + audio_opts + mp4_opts)]
    else:
        candidates = []
        if NVENC_AVAILABLE:
            candidates.append(('transcode-gpu', ['ffmpeg', '-y', '-hwaccel', 'cuda'] + input_args + [
                '-vf', f'scale=-2:{PREVIEW_HEIGHT},format=yuv420p',
                '-c:v', 'h264_nvenc', '-preset', 'p1', '-cq', '30'] + color_opts + audio_opts + mp4_opts))
        candidates.append(('transcode', ['ffmpeg', '-y'] + input_args + [
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
            '-vf', f'scale=-2:{PREVIEW_HEIGHT},format=yuv420p'] + color_opts + audio_opts + mp4_opts))
    tmp = dest + '.tmp'
    last_err = None
    mode = None
    encoder_map = {'stream-copy': 'copy', 'copy-video/aac-audio': 'copy', 'transcode-gpu': 'gpu', 'transcode': 'cpu'}
    for m, cmd in candidates:
        app.logger.info(f"Generating preview ({m}, start={start}, duration={duration}) for {source}")
        try:
            _run_ffmpeg_with_progress(cmd + ['-progress', 'pipe:1', tmp], key, duration, encoder_map.get(m))
            mode = m
            break
        except RuntimeError as e:
            last_err = e
            if m == 'transcode-gpu':
                app.logger.warning(f"GPU preview render failed, falling back to CPU encoding: {e}")
            else:
                raise
    if mode is None:
        raise last_err
    os.replace(tmp, dest)
    app.logger.info(f"Preview ready ({mode}): {dest}")

def _do_generate_preview(source, key, start=None, duration=None):
    dest = _preview_path(key)
    _sanitize_preview_cache()
    max_attempts = 3
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            _generate_preview(source, dest, start, duration, key)
            with _preview_lock:
                _preview_status[key] = {'state': 'ready'}
                _preview_progress.pop(key, None)
            _cleanup_preview_cache()
            return
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                delay = 2 * attempt
                app.logger.warning(f"Preview generation attempt {attempt}/{max_attempts} failed for {source}: {e}. Retrying in {delay}s...")
                time.sleep(delay)
    app.logger.error(f"Preview generation failed after {max_attempts} attempts for {source}: {last_err}")
    with _preview_lock:
        _preview_status[key] = {'state': 'error', 'error': str(last_err)}
        _preview_progress.pop(key, None)

def _sanitize_preview_cache():
    """Remove stale/partial files from the dedicated preview cache dir before a new render."""
    try:
        for f in os.listdir(PREVIEW_CACHE_DIR):
            if f.endswith('.tmp'):
                os.remove(os.path.join(PREVIEW_CACHE_DIR, f))
                app.logger.info(f"Removed stale preview temp file: {f}")
    except Exception as e:
        app.logger.error(f"Preview cache sanitize failed: {e}")

def _cleanup_preview_cache():
    """Keep only the PREVIEW_MAX_CACHE most recent preview files."""
    try:
        files = [os.path.join(PREVIEW_CACHE_DIR, f) for f in os.listdir(PREVIEW_CACHE_DIR) if f.endswith('.mp4')]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[PREVIEW_MAX_CACHE:]:
            os.remove(p)
            app.logger.info(f"Cleaned up old preview: {p}")
    except Exception as e:
        app.logger.error(f"Preview cache cleanup failed: {e}")

@app.route('/preview-source')
def preview_source():
    file = request.args.get('file')
    if not file or not os.path.exists(file):
        return jsonify({'status': 'error', 'message': 'Source file not found'}), 404

    # Parse the requested selection (HH:MM:SS) so we can render just that range + padding
    start_sec = None
    end_sec = None
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    if start_str and end_str:
        try:
            start_sec = max(0.0, timedelta_from_str(start_str).total_seconds())
            end_sec = timedelta_from_str(end_str).total_seconds()
            if end_sec <= start_sec:
                start_sec = None
                end_sec = None
        except Exception:
            start_sec = None
            end_sec = None

    # If the source is already a browser-playable MP4, serve it directly (free scrubbing, offset 0)
    if file.lower().endswith('.mp4'):
        if _probe_codec(file, 'v') == 'h264' and _probe_codec(file, 'a') in ('aac', 'mp3'):
            return jsonify({'status': 'ready', 'url': '/serve?file=' + quote(file), 'offset': 0})

    # Non-browser-playable source: render only the selection + padding (fast) instead of the whole movie
    pad_str = request.args.get('pad')
    pad_seconds = PREVIEW_PAD_SECONDS
    if pad_str:
        try:
            pad_seconds = max(0, int(pad_str))
        except (ValueError, TypeError):
            pad_seconds = PREVIEW_PAD_SECONDS

    if start_sec is not None and end_sec is not None:
        render_start = max(0.0, start_sec - pad_seconds)
        render_duration = (end_sec + pad_seconds) - render_start
        offset = render_start
        key = _preview_cache_key(file, render_start, render_duration)
    else:
        render_start = None
        render_duration = None
        offset = 0
        key = _preview_cache_key(file)

    retry = request.args.get('retry') == '1'
    dest = _preview_path(key)
    with _preview_lock:
        if os.path.exists(dest):
            return jsonify({'status': 'ready', 'url': f'/preview-file/{key}', 'offset': offset})
        st = _preview_status.get(key)
        if st and st['state'] == 'generating':
            prog = _preview_progress.get(key) or {'pct': 0, 'encoder': None}
            return jsonify({'status': 'generating', 'progress': prog.get('pct', 0), 'encoder': prog.get('encoder')})
        if st and st['state'] == 'error' and not retry:
            return jsonify({'status': 'error', 'message': st.get('error', 'preview generation failed')}), 500
        _preview_status[key] = {'state': 'generating'}
        _preview_progress[key] = {'pct': 0, 'encoder': None}
        threading.Thread(target=_do_generate_preview, args=(file, key, render_start, render_duration), daemon=True).start()
    return jsonify({'status': 'generating', 'progress': 0, 'encoder': None})

@app.route('/preview-file/<key>')
def preview_file(key):
    if not re.fullmatch(r'[0-9a-f]{32}', key or ''):
        return 'Bad key', 400
    dest = _preview_path(key)
    if os.path.exists(dest):
        return send_file(dest, mimetype='video/mp4', conditional=True)
    return 'Preview not ready', 404

@app.route('/serve')
def serve():
    file = request.args.get('file')
    app.logger.info(f"Serving file: {file}, from user-agent: {request.user_agent.string}, method: {request.method}")
    if file and os.path.exists(file):
        mime_type = (
            'video/mp4' if file.endswith('.mp4') else
            'image/gif' if file.endswith('.gif') else
            'audio/mpeg' if file.endswith('.mp3') else
            'audio/wav' if file.endswith('.wav') else
            'application/octet-stream'
        )
        app.logger.info(f"Serving file: {file} with MIME type: {mime_type}, exists: {os.path.exists(file)}, size: {os.path.getsize(file) if os.path.exists(file) else 0}")
        try:
            response = send_file(file, mimetype=mime_type, as_attachment=False)
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Disposition'] = 'inline'
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Content-Type'] = mime_type
            response.headers['Cache-Control'] = 'no-cache'
            app.logger.info(f"Response headers: {dict(response.headers)}")
            return response
        except Exception as e:
            app.logger.error(f"Failed to serve file {file}: {str(e)}")
            return 'Error serving file', 500
    app.logger.error(f"File not found for serving: {file}")
    return 'Not found', 404

@app.route('/s3-proxy/<path:filename>')
def s3_proxy(filename):
    if not S3_BUCKET or not S3_ENDPOINT:
        return 'S3 not configured', 500
    try:
        s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name=S3_REGION)
        s3_response = s3.get_object(Bucket=S3_BUCKET, Key=filename)
        mime_type = (
            'video/mp4' if filename.endswith('.mp4') else
            'image/gif' if filename.endswith('.gif') else
            'audio/mpeg' if filename.endswith('.mp3') else
            'audio/wav' if filename.endswith('.wav') else
            'application/octet-stream'
        )
        content_length = s3_response['ContentLength']

        def generate():
            for chunk in s3_response['Body'].iter_chunks(chunk_size=65536):
                yield chunk

        return Response(
            generate(),
            mimetype=mime_type,
            headers={
                'Content-Length': content_length,
                'Cache-Control': 'no-cache'
            }
        )
    except Exception as e:
        app.logger.error(f"S3 proxy failed for {filename}: {str(e)}")
        return 'Proxy error', 500

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000)