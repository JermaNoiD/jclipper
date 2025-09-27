# app.py
import os
import uuid
import subprocess
import threading
import signal
import json
import shutil
import logging
import re
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, session, jsonify, copy_current_request_context
import srt
from urllib.parse import unquote
import boto3

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
app.permanent_session_lifetime = timedelta(minutes=30)
app.logger.setLevel(logging.INFO)

# Register custom Jinja filters
app.jinja_env.filters['basename'] = os.path.basename
app.jinja_env.filters['dirname'] = os.path.dirname
app.jinja_env.filters['unquote'] = unquote

# Environment variables
MOVIES_DIR = os.getenv('MOVIES_DIR', '/movies')
OUTPUT_DIR = os.getenv('OUTPUT_DIR', '/output')
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp/jclipper')
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', 'en')
VIDEO_EXTS = os.getenv('VIDEO_EXTENSIONS', 'mp4,mkv,avi,mov,wmv,flv').split(',')
PREVIEW_RESOLUTION = os.getenv('PREVIEW_RESOLUTION', '1280x720')
S3_ENDPOINT = os.getenv('S3_ENDPOINT')
S3_REGION = os.getenv('S3_REGION')
S3_BUCKET = os.getenv('S3_BUCKET')
S3_KEY = os.getenv('S3_KEY')
S3_SECRET = os.getenv('S3_SECRET')
FFMPEG_LOG_ENABLED = os.getenv('FFMPEG_LOG_ENABLED', 'true').lower() == 'true'
STARTUP_SCAN_LOG_ENABLED = os.getenv('STARTUP_SCAN_LOG_ENABLED', 'true').lower() == 'true'

# Log S3 config and logging status for debugging
app.logger.info(f"S3 Config: ENDPOINT={S3_ENDPOINT}, REGION={S3_REGION}, BUCKET={S3_BUCKET}, KEY={'set' if S3_KEY else 'unset'}, SECRET={'set' if S3_SECRET else 'unset'}")
app.logger.info(f"S3 Enabled: {all([S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_KEY, S3_SECRET])}")
app.logger.info(f"FFmpeg Logging Enabled: {FFMPEG_LOG_ENABLED}")
app.logger.info(f"Startup Scan Logging Enabled: {STARTUP_SCAN_LOG_ENABLED}")

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
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

# Cache movies on startup
movies = []
for dir_name in sorted(os.listdir(MOVIES_DIR)):
    dir_path = os.path.join(MOVIES_DIR, dir_name)
    if os.path.isdir(dir_path):
        videos = [f for f in os.listdir(dir_path) if any(f.lower().endswith(ext) for ext in VIDEO_EXTS)]
        srts = [f for f in os.listdir(dir_path) if f.lower().endswith('.srt')]
        if videos:
            video_path = os.path.join(dir_path, videos[0])
            srt_path = None
            for srt_file in srts:
                if f'.{DEFAULT_LANGUAGE.lower()}.srt' in srt_file.lower():
                    srt_path = os.path.join(dir_path, srt_file)
                    break
            if not srt_path and srts:
                srt_path = os.path.join(dir_path, srts[0])
            movies.append({'name': dir_name, 'video': video_path, 'srt': srt_path, 'has_srt': bool(srts)})
if STARTUP_SCAN_LOG_ENABLED:
    for m in movies:
        app.logger.info(f"Movie: {m['name']}, SRT: {m['srt']}, Video: {m['video']}")

def timedelta_to_srt(t):
    total_seconds = int(t.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int((t.total_seconds() % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

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
    if not request.cookies.get('session'):
        app.logger.info(f"New session detected, clearing TEMP_DIR: {TEMP_DIR}")
        for item in os.listdir(TEMP_DIR):
            item_path = os.path.join(TEMP_DIR, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
                app.logger.info(f"Removed {item} from TEMP_DIR on new session")
            except Exception as e:
                app.logger.error(f"Failed to clear {item} from TEMP_DIR: {str(e)}")
        session['job_dirs'] = {}
        session.modified = True
    app.logger.debug(f"Session state: {session}")

@app.route('/', methods=['GET'])
def index():
    app.logger.info("Serving index page")
    return render_template('index.html', movies=movies)

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
    return render_template('subtitles.html', subs=subs, movie=movie)

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
    available_formats = ['mp4', 'mkv', 'avi', 'mp3']
    source_format = os.path.splitext(video)[1][1:].lower()
    audio_streams = video_info['audio_streams']
    
    # Process audio streams for display
    processed_audio_streams = []
    for s in audio_streams:
        tags = s.get('tags', {})
        lang = tags.get('language', 'Unknown').capitalize()
        processed_audio_streams.append({
            'index': s['index'],
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
                          audio_index=audio_index)

def encode_main(output_file, start_sec, duration, scaled_width, scaled_height, format, video, original_width, original_height, scale_factor, temp_job_dir, audio_index):
    with app.app_context():
        encoding_file = os.path.join(temp_job_dir, 'encoding')
        log_file = os.path.join(temp_job_dir, 'log.txt')
        success_file = os.path.join(temp_job_dir, 'success')
        with open(encoding_file, 'w') as f:
            pass
        scale_filter = f'scale={scaled_width}:{scaled_height}:flags=lanczos' if scale_factor != 1.0 else None
        video_codec = 'libx264' if format in ['mp4', 'mkv'] else 'mpeg4'
        audio_codec = 'aac' if format in ['mp4', 'mkv'] else 'libmp3lame'
        if format == 'mp3':
            main_cmd = [
                'ffmpeg', '-err_detect', 'ignore_err', '-probesize', '100000000', '-analyzeduration', '100000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
                '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:a', 'libmp3lame', '-b:a', '192k', '-ac', '2',
                '-threads', '4', output_file
            ]
        else:
            main_cmd = [
                'ffmpeg', '-err_detect', 'ignore_err', '-probesize', '100000000', '-analyzeduration', '100000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
                '-map', '0:v:0?', '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:v', video_codec, '-preset', 'veryfast', '-c:a', audio_codec, '-b:a', '192k', '-ac', '2',
                '-threads', '4', '-r', '23.98', '-pix_fmt', 'yuv420p', output_file
            ]
            if scale_filter:
                main_cmd.insert(-1, '-vf')
                main_cmd.insert(-1, scale_filter)
            if format == 'mp4':
                main_cmd += ['-movflags', '+faststart']
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"Running FFmpeg for main: {' '.join(main_cmd)}")
        process = subprocess.Popen(main_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
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
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"FFmpeg main completed with returncode: {process.returncode}")
        try:
            os.remove(encoding_file)
        except Exception:
            pass
        stdout = '\n'.join(stdout_lines)
        stderr = '\n'.join(stderr_lines)
        ffmpeg_output = f"stdout: {stdout}\nstderr: {stderr}\nreturncode: {process.returncode}"
        if format != 'mp3' and process.returncode == 0 and os.path.exists(output_file):
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

@app.route('/generate', methods=['POST'])
def generate():
    start_str = request.form.get('start')
    end_str = request.form.get('end')
    video = request.form.get('video')
    format = request.form.get('format', 'mp4')
    padding = float(request.form.get('padding', 0))
    scale_factor = float(request.form.get('scale_factor', 1.0))
    audio_index = request.form.get('audio_index', '0')
    res_str = request.form.get('resolution', '1920x1080')
    scaled_width, scaled_height = map(int, res_str.split('x'))
    app.logger.info(f"Generate request: start={start_str}, end={end_str}, video={video}, format={format}, padding={padding}, scale_factor={scale_factor}, audio_index={audio_index}, resolution={res_str}")
    if not all([start_str, end_str, video]):
        app.logger.warning(f"Missing required params in generate: start={start_str}, end={end_str}, video={video}")
        return redirect(url_for('output'))
    try:
        start_td = timedelta_from_str(start_str)
        end_td = timedelta_from_str(end_str)
        start_sec = max(0, start_td.total_seconds() - padding)
        duration = (end_td.total_seconds() + padding) - start_sec
        app.logger.info(f"Calculated start_sec: {start_sec}, duration: {duration}")
        if duration <= 0:
            app.logger.error("Calculated duration is zero or negative, falling back to 10 seconds")
            duration = 10.0
    except Exception as e:
        app.logger.error(f"Error parsing timestamps: {str(e)} - Falling back to 10 seconds")
        start_sec = 0
        duration = 10.0
    movie_name = os.path.basename(os.path.dirname(video)).replace(' ', '_')
    start_time = start_str.replace(':', '-').replace(',', '.')
    end_time = end_str.replace(':', '-').replace(',', '.')
    res = get_resolution(video)
    original_width, original_height = res
    scaled_width = scaled_width if scaled_width % 2 == 0 else scaled_width - 1
    scaled_height = scaled_height if scaled_height % 2 == 0 else scaled_height - 1
    padding_str = f"p{padding}" if padding > 0 else ""
    safe_filename = f"{movie_name}_{start_time}_to_{end_time}_{scaled_width}x{scaled_height}{padding_str}.{format}"
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
    session['padding'] = padding
    session['scale_factor'] = scale_factor
    session['temp_job_dir'] = temp_job_dir
    session['audio_index'] = audio_index
    session.modified = True
    app.logger.info(f"Preserved session in generate: {session}")
    
    # Generate preview
    preview_filename = "preview.mp4"
    preview_file = os.path.join(temp_job_dir, preview_filename)
    preview_scale_filter = 'scale=1280:-2:flags=lanczos'
    preview_cmd = [
        'ffmpeg', '-err_detect', 'ignore_err', '-probesize', '100000000', '-analyzeduration', '100000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
        '-map', '0:v:0?', '-map', f'0:a:{audio_index}?', '-map', '-0:s?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-vf', preview_scale_filter, '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-threads', '4', '-r', '23.98', preview_file
    ]
    if FFMPEG_LOG_ENABLED:
        app.logger.info(f"Running FFmpeg for preview: {' '.join(preview_cmd)}")
    process = subprocess.run(preview_cmd, capture_output=True, text=True)
    if FFMPEG_LOG_ENABLED:
        app.logger.info(f"FFmpeg preview stdout: {process.stdout}")
    if process.returncode != 0:
        if FFMPEG_LOG_ENABLED:
            app.logger.error(f"FFmpeg preview failed with returncode {process.returncode}: stderr={process.stderr}")
        shutil.rmtree(temp_job_dir)
        return redirect(url_for('output'))
    else:
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"FFmpeg preview stderr (normal output): {process.stderr}")
    app.logger.info(f"Preview file created: {preview_file}, exists: {os.path.exists(preview_file)}, size: {os.path.getsize(preview_file) if os.path.exists(preview_file) else 0}")
    
    if not os.path.exists(preview_file) or os.path.getsize(preview_file) == 0:
        app.logger.error(f"Preview generation failed for {preview_file}")
        shutil.rmtree(temp_job_dir)
        return redirect(url_for('output'))
    
    # Probe (non-mandatory)
    probe_cmd = ['ffprobe', '-err_detect', 'ignore_err', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', preview_file]
    try:
        duration_out = subprocess.check_output(probe_cmd, text=True).strip()
        if FFMPEG_LOG_ENABLED:
            app.logger.info(f"Preview file duration: {duration_out} seconds")
        try:
            duration = float(duration_out)
            if duration == 0:
                app.logger.error(f"Preview file {preview_file} has zero duration")
                os.remove(preview_file)
                shutil.rmtree(temp_job_dir)
                return redirect(url_for('output'))
        except ValueError:
            app.logger.warning(f"Invalid duration output from ffprobe: {duration_out}. Continuing.")
    except subprocess.CalledProcessError as e:
        app.logger.warning(f"Failed to probe preview file {preview_file}: {str(e)}. Continuing since file exists.")
    
    session['preview'] = preview_file
    session['encoding_pid'] = None
    session.modified = True
    
    # Start main encode in background
    threading.Thread(target=copy_current_request_context(encode_main), args=(output_file, start_sec, duration, scaled_width, scaled_height, format, video, original_width, original_height, scale_factor, temp_job_dir, audio_index), daemon=True).start()
    return redirect(url_for('preview', start=start_str, end=end_str, video=video))

@app.route('/preview')
def preview():
    app.logger.info(f"Entering preview route, session: {session}")
    preview = session.get('preview')
    output = session.get('output')
    start = request.args.get('start', session.get('start'))
    end = request.args.get('end', session.get('end'))
    video = request.args.get('video', session.get('movie'))
    if start and end and video:
        session['start'] = start
        session['end'] = end
        session['movie'] = video
        session.modified = True
        app.logger.info(f"Updated session from query params in preview: Start={start}, End={end}, Video={video}, Session={session}")
    if not preview or not os.path.exists(preview):
        app.logger.warning(f"No preview file or file not found: {preview}, redirecting to index")
        return redirect(url_for('index'))
    main_status = 'encoding'
    main_ffmpeg_output = ''
    encoding_done = False
    temp_job_dir = session.get('temp_job_dir')
    if temp_job_dir and output:
        encoding_file = os.path.join(temp_job_dir, 'encoding')
        success_file = os.path.join(temp_job_dir, 'success')
        log_file = os.path.join(temp_job_dir, 'log.txt')
        if os.path.exists(encoding_file):
            main_status = 'encoding'
        elif os.path.exists(success_file):
            main_status = 'success'
            encoding_done = True
        else:
            main_status = 'failure'
            encoding_done = True
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                main_ffmpeg_output = f.read()
    app.logger.info(f"Preview context: preview={preview}, output={output}, encoding_done={encoding_done}, main_status={main_status}")
    s3_enabled = all([S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_KEY, S3_SECRET])
    app.logger.info(f"Preview route: s3_enabled={s3_enabled}")
    return render_template('preview.html', file=preview, output=output, format=session.get('format', 'mp4'), start=start, end=end, video=video, encoding_done=encoding_done, main_status=main_status, main_ffmpeg_output=main_ffmpeg_output, s3_enabled=s3_enabled)

@app.route('/status')
def get_status():
    temp_job_dir = session.get('temp_job_dir')
    if not temp_job_dir:
        return jsonify({'status': 'error', 'message': 'No job directory'})
    
    success_path = os.path.join(temp_job_dir, 'success')  # success indicator file
    log_path = os.path.join(temp_job_dir, 'log.txt')
    
    if os.path.exists(success_path):
        status = 'success'
    else:
        status = 'encoding'  # Add 'failed' logic if you have a .failed file
    
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
        session.pop('preview', None)
        session.pop('output', None)
        session.pop('format', None)
        session.pop('padding', None)
        session.pop('scale_factor', None)
        session.pop('temp_job_dir', None)
        session.pop('audio_index', None)
        session.modified = True
        return response
    except Exception as e:
        app.logger.error(f"Failed to download file {output}: {str(e)}")
        return redirect(url_for('index'))

@app.route('/upload_s3')
def upload_s3():
    output = session.get('output')
    if not output or not os.path.exists(output):
        return jsonify({'success': False, 'message': 'No file to upload'})
    format = session.get('format', 'mp4')
    mime_type = 'video/mp4' if format == 'mp4' else 'video/x-matroska' if format == 'mkv' else 'video/x-msvideo' if format == 'avi' else 'audio/mpeg' if format == 'mp3' else 'application/octet-stream'
    link_format = os.getenv('S3_LINK_FORMAT', 'presigned').lower()  # Default to presigned
    try:
        s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name=S3_REGION)
        # Sanitize movie title up to timestamp
        base_filename = os.path.splitext(os.path.basename(output))[0]
        parts = base_filename.split('_')
        movie_parts = []
        for part in parts:
            if '-' in part and part.replace('.', '').replace('-', '').isdigit():
                break  # Stop at timestamp (e.g., "01-01-59.936")
            movie_parts.append(part)
        movie_title = '_'.join(part for part in movie_parts if part).replace('(', '_').replace(')', '_').replace(' ', '_').replace(',', '_').strip('_')
        movie_folder = f"{movie_title}/"
        video_key = f"{movie_folder}video.{format}"
        s3.upload_file(output, S3_BUCKET, video_key, ExtraArgs={'ContentType': mime_type})
        
        # Generate video URL based on S3_LINK_FORMAT
        if link_format == 'basic':
            video_url = f"{S3_ENDPOINT}/{S3_BUCKET}/{video_key}"
        else:
            video_url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': video_key}, ExpiresIn=604800)
        
        # Verify Content-Type header
        video_response = s3.head_object(Bucket=S3_BUCKET, Key=video_key)
        app.logger.info(f"S3 upload successful, Video URL: {video_url}, Content-Type: {video_response.get('ContentType', 'N/A')}, Link Format: {link_format}")
        
        # Return video URL for clipboard
        return jsonify({'success': True, 'url': video_url})
    except Exception as e:
        app.logger.error(f"S3 upload failed: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/cancel_encoding')
def cancel_encoding():
    app.logger.info("Entering cancel_encoding route")
    pid = session.get('encoding_pid')
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            app.logger.info(f"Cancelled FFmpeg PID: {pid}")
        except Exception as e:
            app.logger.error(f"Failed to cancel PID {pid}: {str(e)}")
    preview = session.get('preview')
    if preview and os.path.exists(preview):
        try:
            os.remove(preview)
            app.logger.info(f"Removed preview file on cancel: {preview}")
        except Exception as e:
            app.logger.error(f"Failed to remove preview file on cancel {preview}: {str(e)}")
    output = session.get('output')
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
        session.modified = True
    session.pop('preview', None)
    session.pop('output', None)
    session.pop('format', None)
    session.pop('padding', None)
    session.pop('scale_factor', None)
    session.pop('temp_job_dir', None)
    session.pop('audio_index', None)
    session.modified = True
    next_page = request.args.get('next', 'index')
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
    return render_template('history.html', file_data=file_data)

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

@app.route('/serve')
def serve():
    file = request.args.get('file')
    app.logger.info(f"Attempting to serve file: {file}")
    if file and os.path.exists(file):
        mime_type = (
            'video/mp4' if file.endswith('.mp4') else
            'video/x-matroska' if file.endswith('.mkv') else
            'video/x-msvideo' if file.endswith('.avi') else
            'audio/mpeg' if file.endswith('.mp3') else
            'application/octet-stream'
        )
        app.logger.info(f"Serving file: {file} with MIME type: {mime_type}")
        try:
            response = send_file(file, mimetype=mime_type, as_attachment=False)
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Disposition'] = 'inline'
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Content-Type'] = mime_type
            response.headers['Cache-Control'] = 'no-cache'
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
        # Construct S3 URL
        s3_url = f"{S3_ENDPOINT}/{S3_BUCKET}/{filename}"
        # Fetch file from S3
        s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name=S3_REGION)
        response = s3.get_object(Bucket=S3_BUCKET, Key=filename)
        content = response['Body'].read()
        
        # Determine MIME type
        mime_type = (
            'video/mp4' if filename.endswith('.mp4') else
            'video/x-matroska' if filename.endswith('.mkv') else
            'video/x-msvideo' if filename.endswith('.avi') else
            'audio/mpeg' if filename.endswith('.mp3') else
            'application/octet-stream'
        )
        
        # Extract metadata from filename (e.g., "Movie_00-01-02_to_00-03-04_1920x1080.mp4")
        parts = filename.rsplit('.', 1)[0].split('_')
        title = parts[0].replace('_', ' ') if parts else 'Video Clip'
        description = f"{title} - Clip from {parts[1] if len(parts) > 1 else 'movie'}" if parts else 'Video Clip'
        
        # Generate thumbnail URL (use FFmpeg to extract a frame if needed, or a placeholder)
        thumbnail_url = f"{request.url_root}s3-proxy-thumbnail/{filename}"  # Optional: Add a thumbnail proxy below
        
        # Return with OG headers
        return Response(
            content,
            mimetype=mime_type,
            headers={
                'og:title': title,
                'og:description': description,
                'og:type': 'video.other',
                'og:video': s3_url,
                'og:image': thumbnail_url,  # Requires a thumbnail image URL
                'Cache-Control': 'no-cache'
            }
        )
    except Exception as e:
        app.logger.error(f"S3 proxy failed for {filename}: {str(e)}")
        return 'Proxy error', 500

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000)