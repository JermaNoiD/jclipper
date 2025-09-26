import os
import uuid
import subprocess
import threading
import signal
import json
import shutil
import logging
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, session, jsonify, copy_current_request_context
import srt
from urllib.parse import unquote

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', str(uuid.uuid4()))
app.permanent_session_lifetime = timedelta(minutes=30)

# Set logger level to capture INFO logs
app.logger.setLevel(logging.INFO)

# Environment variables
MOVIES_DIR = os.getenv('MOVIES_DIR', '/movies')
OUTPUT_DIR = os.getenv('OUTPUT_DIR', '/output')
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp/jclipper')
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', 'en')
VIDEO_EXTS = os.getenv('VIDEO_EXTENSIONS', 'mp4,mkv,avi,mov,wmv,flv').split(',')
PREVIEW_RESOLUTION = os.getenv('PREVIEW_RESOLUTION', '1280x720')

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
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
        app.logger.info(f"Cleared {item} from TEMP_DIR on startup")
    except Exception as e:
        app.logger.error(f"Failed to clear {item} from TEMP_DIR on startup: {str(e)}")

# Cache for resolutions and audio streams
resolution_cache = {}
audio_streams_cache = {}

# Cache movies on startup
movies = []
for dir_name in sorted(os.listdir(MOVIES_DIR)):
    dir_path = os.path.join(MOVIES_DIR, dir_name)
    if os.path.isdir(dir_path):
        videos = [f for f in os.listdir(dir_path) if any(f.lower().endswith(ext) for ext in VIDEO_EXTS)]
        srts = [f for f in os.listdir(dir_path) if f.lower().endswith('.srt')]
        if videos:
            video_path = os.path.join(dir_path, videos[0])
            # Prioritize SRT with DEFAULT_LANGUAGE
            srt_path = None
            for srt_file in srts:
                if f'.{DEFAULT_LANGUAGE.lower()}.srt' in srt_file.lower():
                    srt_path = os.path.join(dir_path, srt_file)
                    break
            if not srt_path and srts:
                srt_path = os.path.join(dir_path, srts[0])  # Fallback to first SRT
            movies.append({'name': dir_name, 'video': video_path, 'srt': srt_path, 'has_srt': bool(srts)})
for m in movies:
    app.logger.info(f"Movie: {m['name']}, SRT: {m['srt']}, Video: {m['video']}")

def timedelta_to_srt(t):
    total_seconds = int(t.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int((t.total_seconds() % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def get_resolution(video):
    if video not in resolution_cache:
        cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video]
        app.logger.info(f"Running ffprobe command: {' '.join(cmd)}")
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
            app.logger.info(f"ffprobe raw output: {out}")
            if not out or 'x' not in out:
                app.logger.error(f"Invalid ffprobe output for {video}: {out}")
                resolution_cache[video] = [1920, 1080]
            else:
                width, height = map(int, out.split('x'))
                resolution_cache[video] = [width, height]
        except subprocess.CalledProcessError as e:
            app.logger.error(f"ffprobe failed for {video}: {e.output}")
            resolution_cache[video] = [1920, 1080]
        except ValueError as e:
            app.logger.error(f"Failed to parse resolution for {video}: {out} - {str(e)}")
            resolution_cache[video] = [1920, 1080]
        except Exception as e:
            app.logger.error(f"Error getting resolution for {video}: {str(e)}")
            resolution_cache[video] = [1920, 1080]
    return resolution_cache[video]

def get_audio_streams(video):
    if video not in audio_streams_cache:
        cmd = ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index,codec_name,channels,bit_rate,tags', '-of', 'json', video]
        app.logger.info(f"Running ffprobe for audio streams: {' '.join(cmd)}")
        try:
            out = subprocess.check_output(cmd, text=True).strip()
            data = json.loads(out)
            streams = data.get('streams', [])
            audio_streams_cache[video] = streams
        except subprocess.CalledProcessError as e:
            app.logger.error(f"ffprobe failed for audio streams on {video}: {e.output}")
            audio_streams_cache[video] = []
        except Exception as e:
            app.logger.error(f"Error getting audio streams for {video}: {str(e)}")
            audio_streams_cache[video] = []
    return audio_streams_cache[video]

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
        # Initialize job_dirs in session
        session['job_dirs'] = {}
        session.modified = True
    app.logger.info(f"Session state: {session}")

@app.route('/')
def index():
    app.logger.info("Serving index page")
    error_message = session.pop('error_message', None)
    return render_template('index.html', movies=movies, error_message=error_message)

@app.route('/subtitles/<name>')
def subtitles(name):
    name = unquote(name)
    app.logger.info(f"Accessing subtitles for movie: {name}")
    movie = next((m for m in movies if m['name'] == name), None)
    if not movie:
        app.logger.error(f"Movie not found: {name}")
        return 'Movie not found', 404
    if not movie['has_srt']:
        app.logger.warning(f"No subtitles for movie: {name}")
        session['error_message'] = f"No subtitles available for {name}. Check if an SRT file exists in the movie directory."
        session.modified = True
        return redirect(url_for('index'))
    try:
        app.logger.info(f"Attempting to open SRT file: {movie['srt']}")
        with open(movie['srt'], 'r', encoding='utf-8', errors='ignore') as f:
            srt_content = f.read()
            app.logger.info(f"Read SRT content (first 100 chars): {srt_content[:100]}")
            subs = list(srt.parse(srt_content))
        if not subs:
            raise ValueError("No valid subtitles found in SRT file")
        for sub in subs:
            sub.start_str = timedelta_to_srt(sub.start)
            sub.end_str = timedelta_to_srt(sub.end)
        session['movie'] = movie['video']
        session.modified = True
        app.logger.info(f"Session after setting movie: {session}")
        return render_template('subtitles.html', subs=subs, name=name, video=movie['video'])
    except Exception as e:
        app.logger.error(f"Error reading SRT for {name}: {str(e)}")
        session['error_message'] = f"Error reading subtitles for {name}: {str(e)}. Check if the SRT file is valid UTF-8 and properly formatted. Ensure the file is not corrupted and try re-downloading it if necessary."
        session.modified = True
        return redirect(url_for('index'))

@app.route('/output', methods=['GET', 'POST'])
def output():
    app.logger.info(f"Received request in output: method={request.method}, form={request.form.to_dict()}, args={request.args.to_dict()}")
    if request.method == 'POST':
        start_str = request.form.get('start')
        end_str = request.form.get('end')
        video = request.form.get('video')
        audio_index = request.form.get('audio_index')
        app.logger.info(f"Form data: start={start_str}, end={end_str}, video={video}, audio_index={audio_index}")
        if start_str and end_str and video:
            session['start'] = start_str
            session['end'] = end_str
            session['movie'] = video
            if audio_index:
                session['audio_index'] = audio_index
            session.modified = True
            app.logger.info(f"Output page: Start={start_str}, End={end_str}, Video={video}, Audio Index={audio_index}, Session={session}")
        else:
            app.logger.warning(f"Start, end, or video not found in form data, form={request.form.to_dict()}")
    start = session.get('start')
    end = session.get('end')
    video = session.get('movie')
    audio_index = session.get('audio_index', '0')
    if not start or not end or not video:
        start = request.args.get('start', start)
        end = request.args.get('end', end)
        video = request.args.get('video', video)
        audio_index = request.args.get('audio_index', audio_index)
        if start and end and video:
            session['start'] = start
            session['end'] = end
            session['movie'] = video
            session['audio_index'] = audio_index
            session.modified = True
            app.logger.info(f"Updated session from query params: Start={start}, End={end}, Video={video}, Audio Index={audio_index}, Session={session}")
        else:
            app.logger.warning(f"Query params insufficient - start: {start}, end: {end}, video: {video}, audio_index: {audio_index}")
    app.logger.info(f"Session in output before check: {session}")
    if not all([start, end, video]):
        app.logger.warning(f"Missing session data - start: {start}, end: {end}, video: {video}, redirecting to index")
        return redirect(url_for('index'))
    res = get_resolution(video)
    app.logger.info(f"Using resolution for {video}: {res[0]}x{res[1]}")
    source_format = os.path.splitext(video)[1].lstrip('.').lower()
    available_formats = ['mp4', 'mkv', 'avi', 'mp3']
    default_format = 'mp4'
    audio_streams = get_audio_streams(video)
    has_multiple_audio = len(audio_streams) > 1
    default_audio_index = 0
    for idx, stream in enumerate(audio_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        if lang.lower() == DEFAULT_LANGUAGE.lower():
            default_audio_index = idx
            break
    return render_template('output.html', original_res=res, start=start, end=end, video=video, default_format=default_format, available_formats=available_formats, source_format=source_format, has_multiple_audio=has_multiple_audio, audio_streams=audio_streams, default_audio_index=default_audio_index, selected_audio_index=audio_index)

def encode_main(output_file, start_sec, duration, scaled_width, scaled_height, format, video, original_width, original_height, scale_factor, temp_job_dir, audio_index):
    with app.app_context():
        encoding_file = os.path.join(temp_job_dir, 'encoding')
        log_file = os.path.join(temp_job_dir, 'log.txt')
        success_file = os.path.join(temp_job_dir, 'success')
        # Mark encoding start
        with open(encoding_file, 'w') as f:
            pass
        scale_filter = f'scale={scaled_width}:{scaled_height}:flags=lanczos' if scale_factor != 1.0 else None
        video_codec = 'libx264' if format in ['mp4', 'mkv'] else 'mpeg4'
        audio_codec = 'aac' if format in ['mp4', 'mkv'] else 'libmp3lame'
        if format == 'mp3':
            main_cmd = [
                'ffmpeg', '-probesize', '50000000', '-analyzeduration', '50000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
                '-map', f'0:a:{audio_index}?', '-c:a', 'libmp3lame', '-b:a', '192k', '-ac', '2',
                '-threads', '4', output_file
            ]
        else:
            main_cmd = [
                'ffmpeg', '-probesize', '50000000', '-analyzeduration', '50000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
                '-map', '0:v:0?', '-map', f'0:a:{audio_index}?', '-c:v', video_codec, '-preset', 'veryfast', '-c:a', audio_codec, '-b:a', '192k', '-ac', '2',
                '-threads', '4', '-r', '23.98', '-pix_fmt', 'yuv420p', output_file
            ]
            if scale_filter:
                main_cmd.insert(-1, '-vf')
                main_cmd.insert(-1, scale_filter)
            if format == 'mp4':
                main_cmd += ['-movflags', '+faststart']
        app.logger.info(f"Running FFmpeg for main: {' '.join(main_cmd)}")
        process = subprocess.Popen(main_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
        stdout_lines = []
        stderr_lines = []
        for line in iter(process.stderr.readline, ''):
            app.logger.info(f"FFmpeg main stderr line: {line.strip()}")
            stderr_lines.append(line.strip())
        for line in iter(process.stdout.readline, ''):
            app.logger.info(f"FFmpeg main stdout line: {line.strip()}")
            stdout_lines.append(line.strip())
        process.wait()
        app.logger.info(f"FFmpeg main completed with returncode: {process.returncode}")
        # Remove encoding marker
        try:
            os.remove(encoding_file)
        except Exception:
            pass
        stdout = '\n'.join(stdout_lines)
        stderr = '\n'.join(stderr_lines)
        ffmpeg_output = f"stdout: {stdout}\nstderr: {stderr}\nreturncode: {process.returncode}"
        # Verify output resolution
        if format != 'mp3' and process.returncode == 0 and os.path.exists(output_file):
            probe_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', output_file]
            try:
                res_out = subprocess.check_output(probe_cmd, text=True).strip()
                app.logger.info(f"Output file resolution: {res_out}")
                ffmpeg_output += f"\nOutput resolution: {res_out}"
            except subprocess.CalledProcessError as e:
                app.logger.error(f"Failed to probe output file resolution: {e.output}")
                ffmpeg_output += "\nOutput resolution: Failed to probe"
        with open(log_file, 'w') as f:
            f.write(ffmpeg_output)
        if process.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            open(success_file, 'w').close()
            # Store temp_job_dir in session
            session['job_dirs'] = session.get('job_dirs', {})
            session['job_dirs'][output_file] = temp_job_dir
            session.modified = True
            app.logger.info(f"Main output file: {output_file}, size: {os.path.getsize(output_file)}, temp_job_dir: {temp_job_dir}")
        else:
            app.logger.error(f"Main encoding failed or file missing: returncode={process.returncode}, exists={os.path.exists(output_file)}")

@app.route('/generate', methods=['POST'])
def generate():
    app.logger.info("Entered /generate route")
    padding = int(request.form.get('padding', 0))
    format = request.form.get('format', 'mp4')
    scale_factor = request.form.get('scale', '1.0')
    audio_index = request.form.get('audio_index', '0')
    try:
        scale_factor = float(scale_factor)
        if scale_factor <= 0 or scale_factor > 1.0:
            app.logger.warning(f"Invalid scale factor {scale_factor}, falling back to 1.0")
            scale_factor = 1.0
    except ValueError:
        app.logger.error(f"Invalid scale factor value {scale_factor}, falling back to 1.0")
        scale_factor = 1.0
    start_str = request.form.get('start')
    end_str = request.form.get('end')
    video = request.form.get('video')
    app.logger.info(f"Form data in generate: start={start_str}, end={end_str}, video={video}, scale={scale_factor}, format={format}, audio_index={audio_index}")
    if not start_str or not end_str or not video:
        start_str = session.get('start')
        end_str = session.get('end')
        video = session.get('movie')
        audio_index = session.get('audio_index', '0')
        if not start_str or not end_str or not video:
            app.logger.error(f"Missing start, end, or video in form and session - start: {start_str}, end={end_str}, video: {video}")
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
    scaled_width = int(original_width * scale_factor)
    scaled_height = int(original_height * scale_factor)
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
        'ffmpeg', '-probesize', '50000000', '-analyzeduration', '50000000', '-ss', str(start_sec), '-i', video, '-t', str(duration),
        '-map', '0:v:0?', '-map', f'0:a:{audio_index}?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-vf', preview_scale_filter, '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-threads', '4', '-r', '23.98', preview_file
    ]
    app.logger.info(f"Running FFmpeg for preview: {' '.join(preview_cmd)}")
    process = subprocess.run(preview_cmd, capture_output=True, text=True)
    app.logger.info(f"FFmpeg preview stdout: {process.stdout}")
    if process.returncode != 0:
        app.logger.error(f"FFmpeg preview failed with returncode {process.returncode}: stderr={process.stderr}")
        shutil.rmtree(temp_job_dir)
        return redirect(url_for('output'))
    else:
        app.logger.info(f"FFmpeg preview stderr (normal output): {process.stderr}")
    app.logger.info(f"Preview file created: {preview_file}, exists: {os.path.exists(preview_file)}, size: {os.path.getsize(preview_file) if os.path.exists(preview_file) else 0}")
    
    if not os.path.exists(preview_file) or os.path.getsize(preview_file) == 0:
        app.logger.error(f"Preview generation failed for {preview_file}")
        shutil.rmtree(temp_job_dir)
        return redirect(url_for('output'))
    
    # Probe (non-mandatory)
    probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', preview_file]
    try:
        duration_out = subprocess.check_output(probe_cmd, text=True).strip()
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
    return render_template('preview.html', file=preview, output=output, format=session.get('format', 'mp4'), start=start, end=end, video=video, encoding_done=encoding_done, main_status=main_status, main_ffmpeg_output=main_ffmpeg_output)

@app.route('/status')
def status():
    output = session.get('output')
    if not output:
        return jsonify({'status': 'unknown', 'ffmpeg_output': ''})
    temp_job_dir = session.get('temp_job_dir')
    if not temp_job_dir:
        return jsonify({'status': 'unknown', 'ffmpeg_output': ''})
    encoding_file = os.path.join(temp_job_dir, 'encoding')
    success_file = os.path.join(temp_job_dir, 'success')
    log_file = os.path.join(temp_job_dir, 'log.txt')
    status = 'encoding' if os.path.exists(encoding_file) else 'unknown'
    ffmpeg_output = ''
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            ffmpeg_output = f.read()
        if os.path.exists(success_file):
            status = 'success'
        else:
            status = 'failure'
    elif not os.path.exists(encoding_file):
        status = 'failure'
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
        # Do not immediately remove preview or other temp files
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
    session.pop('encoding_pid', None)
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
    # Delete all files in OUTPUT_DIR
    for f in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, f)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                app.logger.info(f"Successfully deleted {file_path}")
            except Exception as e:
                app.logger.error(f"Failed to delete {file_path}: {str(e)}")
                success = False
    # Delete all in TEMP_DIR
    try:
        shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)
        app.logger.info("Cleared TEMP_DIR")
    except Exception as e:
        app.logger.error(f"Failed to clear TEMP_DIR: {str(e)}")
        success = False
    # Clear session job_dirs
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

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000)