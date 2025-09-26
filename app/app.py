import os
import uuid
import subprocess
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, session, jsonify
import srt
from urllib.parse import unquote

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', str(uuid.uuid4()))  # Use environment variable or generate UUID
app.permanent_session_lifetime = timedelta(minutes=30)  # Set session lifetime at app level

# Get environment variables with defaults
MOVIES_DIR = os.getenv('MOVIES_DIR', '/movies')
TMP_DIR = os.getenv('TMP_DIR', '/tmp/output')
VIDEO_EXTS = os.getenv('VIDEO_EXTENSIONS', 'mp4,mkv,avi,mov,wmv,flv').split(',')

# Ensure temporary directory exists without deleting contents
os.makedirs(TMP_DIR, exist_ok=True)
app.logger.info(f"Startup: TMP_DIR {TMP_DIR} exists with contents: {os.listdir(TMP_DIR)}")

# Cache for native resolutions to avoid repeated ffprobe calls
resolution_cache = {}

# Cache movies on startup
movies = []
for dir_name in sorted(os.listdir(MOVIES_DIR)):
    dir_path = os.path.join(MOVIES_DIR, dir_name)
    if os.path.isdir(dir_path):
        videos = [f for f in os.listdir(dir_path) if any(f.lower().endswith(ext) for ext in VIDEO_EXTS)]
        srts = [f for f in os.listdir(dir_path) if f.lower().endswith('.en.srt') or f.lower().endswith('.srt')]
        if videos:
            video_path = os.path.join(dir_path, videos[0])
            srt_path = os.path.join(dir_path, srts[0]) if srts else None
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

@app.before_request
def make_session_permanent():
    session.permanent = True  # Set permanence within request context
    app.logger.info(f"Session state in before_request: {session}")  # Debug session state

@app.route('/')
def index():
    app.logger.info("Serving index page")
    return render_template('index.html', movies=movies)

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
        return 'No subtitles available', 400
    try:
        app.logger.info(f"Attempting to open SRT file: {movie['srt']}")
        with open(movie['srt'], 'r', encoding='utf-8', errors='ignore') as f:
            subs = list(srt.parse(f))
        for sub in subs:
            sub.start_str = timedelta_to_srt(sub.start)
            sub.end_str = timedelta_to_srt(sub.end)
        session['movie'] = movie['video']
        session.modified = True  # Explicitly mark session as modified to ensure saving
        app.logger.info(f"Session after setting movie in subtitles: {session}")
        return render_template('subtitles.html', subs=subs, name=name, video=movie['video'])
    except FileNotFoundError as e:
        app.logger.error(f"SRT file not found at {movie['srt']}: {str(e)}")
        return f'SRT file not found: {movie["srt"]}', 500
    except Exception as e:
        app.logger.error(f"Error reading SRT for {name}: {str(e)}")
        return 'Error reading subtitles', 500

@app.route('/output', methods=['GET', 'POST'])
def output():
    app.logger.info(f"Received request in output: method={request.method}, form={request.form.to_dict()}, args={request.args.to_dict()}")
    if request.method == 'POST':
        start_str = request.form.get('start')
        end_str = request.form.get('end')
        video = request.form.get('video')  # Get video from form
        app.logger.info(f"Form data: start={start_str}, end={end_str}, video={video}")
        if start_str and end_str and video:
            session['start'] = start_str
            session['end'] = end_str
            session['movie'] = video  # Update session with video
            session.modified = True  # Ensure session is saved
            app.logger.info(f"Output page: Start={start_str}, End={end_str}, Video={video}, Session={session}")
        else:
            app.logger.warning(f"Start, end, or video not found in form data, form={request.form.to_dict()}")
    start = session.get('start')
    end = session.get('end')
    video = session.get('movie')
    # Fallback to query parameters if session data is missing
    if not start or not end or not video:
        start = request.args.get('start', start)
        end = request.args.get('end', end)
        video = request.args.get('video', video)
        if start and end and video:
            session['start'] = start
            session['end'] = end
            session['movie'] = video
            session.modified = True  # Ensure session is saved
            app.logger.info(f"Updated session from query params: Start={start}, End={end}, Video={video}, Session={session}")
        else:
            app.logger.warning(f"Query params insufficient - start: {start}, end: {end}, video: {video}")
    app.logger.info(f"Session in output before check: {session}")
    if not all([start, end, video]):
        app.logger.warning(f"Missing session data - start: {start}, end: {end}, video: {video}, redirecting to index")
        return redirect(url_for('index'))
    res = get_resolution(video)
    app.logger.info(f"Using resolution for {video}: {res[0]}x{res[1]}")
    return render_template('output.html', original_res=res, start=start, end=end, video=video)

@app.route('/generate', methods=['POST'])
def generate():
    app.logger.info("Entered /generate route")
    padding = int(request.form.get('padding', 0))
    format = request.form.get('format', 'mp4')
    scale_factor = request.form.get('scale', '1.0')  # Default to 1.0 as string, convert later
    try:
        scale_factor = float(scale_factor)
        if scale_factor <= 0 or scale_factor > 2.0:  # Reasonable range
            app.logger.warning(f"Invalid scale factor {scale_factor}, falling back to 1.0")
            scale_factor = 1.0
    except ValueError:
        app.logger.error(f"Invalid scale factor value {scale_factor}, falling back to 1.0")
        scale_factor = 1.0
    start_str = request.form.get('start')  # Use form data first
    end_str = request.form.get('end')     # Use form data first
    video = request.form.get('video')     # Use form data first
    app.logger.info(f"Form data in generate: start={start_str}, end={end_str}, video={video}, scale={scale_factor}, session={session}")
    if not start_str or not end_str or not video:
        start_str = session.get('start')  # Fallback to session
        end_str = session.get('end')     # Fallback to session
        video = session.get('movie')     # Fallback to session
        if not start_str or not end_str or not video:
            app.logger.error(f"Missing start, end, or video in form and session - start: {start_str}, end={end_str}, video: {video}")
            return redirect(url_for('output'))  # Redirect back to output to retry
    # Preserve session data before redirecting
    session['start'] = start_str
    session['end'] = end_str
    session['movie'] = video
    session.modified = True  # Ensure session is saved
    app.logger.info(f"Preserved session in generate: {session}")
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
    # Extract movie name from video path and create meaningful filename
    movie_name = os.path.basename(os.path.dirname(video)).replace(' ', '_')
    start_time = start_str.replace(':', '-').replace(',', '.')
    end_time = end_str.replace(':', '-').replace(',', '.')
    res = get_resolution(video)
    original_width, original_height = res
    resolution = f"{original_width}x{original_height}"
    scaled_width = int(original_width * scale_factor)
    scaled_height = int(original_height * scale_factor)
    # Ensure even dimensions for compatibility
    scaled_width = scaled_width if scaled_width % 2 == 0 else scaled_width - 1
    scaled_height = scaled_height if scaled_height % 2 == 0 else scaled_height - 1
    padding_str = f"p{padding}" if padding > 0 else ""
    safe_filename = f"{movie_name}_{start_time}_to_{end_time}_{scaled_width}x{scaled_height}{padding_str}.{format}"
    output_file = os.path.join(TMP_DIR, safe_filename)
    # Adjust scale filter for valid output
    scale_filter = f'scale={scaled_width}:{scaled_height}:flags=lanczos' if scale_factor != 1.0 else f'scale={original_width}:{original_height}'
    cmd = [
        'ffmpeg', '-ss', str(start_sec), '-i', video, '-t', str(duration),
        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k', '-ac', '2',
        '-threads', '4', '-r', '23.98', '-vf', scale_filter, '-movflags', '+faststart', '-probesize', '10000000', '-analyzeduration', '10000000', output_file
    ]
    app.logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
    process = subprocess.run(cmd, capture_output=True, text=True)
    app.logger.info(f"FFmpeg stdout: {process.stdout}")
    app.logger.error(f"FFmpeg stderr: {process.stderr}")
    if process.returncode != 0:
        app.logger.error(f"FFmpeg failed with return code {process.returncode}")
        # Fallback to original resolution if scaling fails
        if scale_factor != 1.0:
            app.logger.warning(f"Scaling failed, falling back to original resolution")
            scale_filter = f'scale={original_width}:{original_height}'
            cmd = [
                'ffmpeg', '-ss', str(start_sec), '-i', video, '-t', str(duration),
                '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k', '-ac', '2',
                '-threads', '4', '-r', '23.98', '-vf', scale_filter, '-movflags', '+faststart', '-probesize', '10000000', '-analyzeduration', '10000000', output_file
            ]
            app.logger.info(f"Retry FFmpeg command: {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True)
            app.logger.info(f"Retry FFmpeg stdout: {process.stdout}")
            app.logger.error(f"Retry FFmpeg stderr: {process.stderr}")
            if process.returncode != 0:
                app.logger.error(f"Retry with original resolution failed with return code {process.returncode}")
    if os.path.exists(output_file):
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', output_file]
        try:
            duration_out = subprocess.check_output(probe_cmd, text=True).strip()
            app.logger.info(f"Output file duration: {duration_out} seconds")
            if float(duration_out) == 0:
                app.logger.error(f"Output file {output_file} has zero duration")
                os.remove(output_file)
        except subprocess.CalledProcessError as e:
            app.logger.error(f"Failed to probe output file {output_file}: {str(e)}")
            if os.path.exists(output_file):
                os.remove(output_file)
    else:
        app.logger.error(f"Output file {output_file} was not created")
    session['output'] = output_file if os.path.exists(output_file) else None
    session.modified = True  # Ensure session is saved with output file path
    app.logger.info(f"Session after setting output: {session}")
    return redirect(url_for('preview', file=output_file if os.path.exists(output_file) else '', start=start_str, end=end_str, video=video))

@app.route('/preview')
def preview():
    app.logger.info(f"Entering preview route, session: {session}")
    output = session.get('output')
    app.logger.info(f"Preview file from session: {output}")
    if not output:
        app.logger.warning("No output file in session, redirecting to index")
        return redirect(url_for('index'))
    format = output.split('.')[-1]
    # Use query parameters as primary source
    start = request.args.get('start', session.get('start'))
    end = request.args.get('end', session.get('end'))
    video = request.args.get('video', session.get('movie'))
    if start and end and video:
        session['start'] = start
        session['end'] = end
        session['movie'] = video
        session.modified = True  # Ensure session is saved
        app.logger.info(f"Updated session from query params in preview: Start={start}, End={end}, Video={video}, Session={session}")
    app.logger.info(f"Preview context: start={start}, end={end}, video={video}")
    return render_template('preview.html', file=output, format=format, start=start, end=end, video=video)

@app.route('/download')
def download():
    app.logger.info(f"Entering download route, session: {session}")
    output = session.get('output')
    app.logger.info(f"Output file from session: {output}")
    # Fallback to query parameter if session is None
    if not output:
        output = request.args.get('file')
        app.logger.info(f"Fallback to query parameter file: {output}")
    if not output or not os.path.exists(output):
        app.logger.warning(f"No output file to download or file not found: {output}, redirecting to index")
        return redirect(url_for('index'))
    try:
        app.logger.info(f"Downloading file: {output}")
        response = send_file(output, as_attachment=True, download_name=os.path.basename(output))
        response.headers['Content-Disposition'] = f'attachment; filename="{os.path.basename(output)}"'
        return response
    except Exception as e:
        app.logger.error(f"Failed to download file {output}: {str(e)}")
        return redirect(url_for('index'))
    finally:
        if 'response' in locals() and response.status_code == 200 and os.path.exists(output):
            os.remove(output)
            session.pop('output', None)
            app.logger.info("File removed and session cleared after successful download")

@app.route('/resolution')
def resolution():
    video = session.get('movie')
    scale = float(request.args.get('scale', 1.0))
    res = get_resolution(video)
    w, h = int(res[0] * scale), int(res[1] * scale)
    return jsonify({'scaled': f'{w}x{h}'})

@app.route('/history')
def history():
    app.logger.info("Serving history page")
    output_files = [f for f in os.listdir(TMP_DIR) if os.path.isfile(os.path.join(TMP_DIR, f))]
    full_paths = [os.path.join(TMP_DIR, f) for f in output_files]
    file_data = [(full_path, os.path.basename(full_path)) for full_path in full_paths]  # Pair full paths with basenames
    app.logger.info(f"Found output files: {[basename for _, basename in file_data]}")
    return render_template('history.html', file_data=file_data)

@app.route('/delete', methods=['POST'])
def delete():
    file_path = request.form.get('file_path')
    app.logger.info(f"Attempting to delete file: {file_path}")
    if file_path and file_path.startswith(TMP_DIR):
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                app.logger.info(f"Successfully deleted file: {file_path}")
                return jsonify({'success': True})
            except Exception as e:
                app.logger.error(f"Failed to delete file {file_path}: {str(e)}")
                return jsonify({'success': False, 'message': 'Deletion failed'})
        else:
            app.logger.warning(f"File not found for deletion: {file_path}")
            return jsonify({'success': True})  # Return success if file already deleted
    else:
        app.logger.warning(f"Invalid or non-existent file path: {file_path}")
        return jsonify({'success': False, 'message': 'Invalid file path'})

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
        mime_type = 'video/mp4' if file.endswith('.mp4') else 'audio/mp3'
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
    app.run(host='0.0.0.0', port=5000)  # Debug mode for development