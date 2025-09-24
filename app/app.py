import os
import uuid
import subprocess
import re
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, session, jsonify
import srt
from dotenv import load_dotenv
from urllib.parse import unquote

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = 'secret'

# Get environment variables
MOVIES_DIR = os.getenv('MOVIES_DIR', '/movies')
TMP_DIR = os.getenv('TMP_DIR', '/tmp/output')
VIDEO_EXTS = os.getenv('VIDEO_EXTENSIONS', 'mp4,mkv,avi,mov,wmv,flv').split(',')
UNIFORM_RESOLUTION = os.getenv('UNIFORM_RESOLUTION', '1280:720')

# Ensure temporary directory exists
os.makedirs(TMP_DIR, exist_ok=True)

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
    app.logger.info(f"Movie: {m['name']}, SRT: {m['srt']}, Video: {m['video']}")  # Debug movie cache

# Custom template filter to fix static paths
""" @app.template_filter('fix_static_paths')
def fix_static_paths(template_content):
    app.logger.info("Applying fix_static_paths filter")
    # Log original content (first 1000 chars for brevity)
    app.logger.debug(f"Original template content (truncated): {template_content[:1000]}")
    
    # Replace href="[static|STATIC]/[bootstrap|css|CSS]/.../*.css" (case-insensitive, optional leading /)
    template_content = re.sub(
        r'href=["\']/?[sS][tT][aA][tT][iI][cC]/[bB][oO][oO][tT][sS][tT][rR][aA][pP]/[cC][sS][sS]/([^"\']+)\.css["\']',
        r'href="{{ url_for(\'static\', filename=\'bootstrap/css/\1.css\') }}"',
        template_content,
        flags=re.IGNORECASE
    )
    template_content = re.sub(
        r'href=["\']/?[sS][tT][aA][tT][iI][cC]/[cC][sS][sS]/([^"\']+)\.css["\']',
        r'href="{{ url_for(\'static\', filename=\'css/\1.css\') }}"',
        template_content,
        flags=re.IGNORECASE
    )
    
    # Replace src="[static|STATIC]/[bootstrap|js|JS]/.../*.js" (case-insensitive, optional leading /)
    template_content = re.sub(
        r'src=["\']/?[sS][tT][aA][tT][iI][cC]/[bB][oO][oO][tT][sS][tT][rR][aA][pP]/[jJ][sS]/([^"\']+)\.js["\']',
        r'src="{{ url_for(\'static\', filename=\'bootstrap/js/\1.js\') }}"',
        template_content,
        flags=re.IGNORECASE
    )
    template_content = re.sub(
        r'src=["\']/?[sS][tT][aA][tT][iI][cC]/[jJ][sS]/([^"\']+)\.js["\']',
        r'src="{{ url_for(\'static\', filename=\'js/\1.js\') }}"',
        template_content,
        flags=re.IGNORECASE
    )
    
    # Replace img src="[static|STATIC]/img/.../*" (case-insensitive, optional leading /)
    template_content = re.sub(
        r'src=["\']/?[sS][tT][aA][tT][iI][cC]/[iI][mM][gG]/([^"\']+)["\']',
        r'src="{{ url_for(\'static\', filename=\'img/\1\') }}"',
        template_content,
        flags=re.IGNORECASE
    )
    
    # Log modified content (first 1000 chars for brevity)
    app.logger.debug(f"Modified template content (truncated): {template_content[:1000]}")
    return template_content
 """
# Override render_template to apply the filter
def render_template_patched(template_name, **context):
    app.logger.info(f"Rendering template: {template_name}")
    try:
        # Load the template content
        template_path = os.path.join(app.template_folder, template_name)
        if not os.path.exists(template_path):
            app.logger.error(f"Template file not found: {template_path}")
            return f"Template {template_name} not found", 500
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        # Apply the fix_static_paths filter
        template_content = fix_static_paths(template_content)
        # Write to a temporary file for rendering
        temp_file = os.path.join(app.template_folder, f'temp_{uuid.uuid4().hex}_{template_name}')
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(template_content)
        try:
            return render_template(temp_file, **context)
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    app.logger.debug(f"Cleaned up temporary file: {temp_file}")
                except Exception as e:
                    app.logger.error(f"Failed to clean up temporary file {temp_file}: {str(e)}")
    except Exception as e:
        app.logger.error(f"Error processing template {template_name}: {str(e)}")
        return f"Error rendering template: {str(e)}", 500

# Patch Flask's render_template
app.jinja_env.globals['render_template'] = render_template_patched

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
        app.logger.info(f"Running ffprobe command: {' '.join(cmd)}")  # Debug command
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
            app.logger.info(f"ffprobe raw output: {out}")  # Log raw output
            if not out or 'x' not in out:
                app.logger.error(f"Invalid ffprobe output for {video}: {out}")
                resolution_cache[video] = [1920, 1080]  # Fallback to a common HD resolution
            else:
                width, height = map(int, out.split('x'))
                resolution_cache[video] = [width, height]
        except subprocess.CalledProcessError as e:
            app.logger.error(f"ffprobe failed for {video}: {e.output}")
            resolution_cache[video] = [1920, 1080]  # Fallback on failure
        except ValueError as e:
            app.logger.error(f"Failed to parse resolution for {video}: {out} - {str(e)}")
            resolution_cache[video] = [1920, 1080]  # Fallback on parsing error
        except Exception as e:
            app.logger.error(f"Error getting resolution for {video}: {str(e)}")
            resolution_cache[video] = [1920, 1080]  # Fallback on error
    return resolution_cache[video]

@app.route('/')
def index():
    return render_template('index.html', movies=movies)

@app.route('/subtitles/<name>')
def subtitles(name):
    name = unquote(name)  # Decode URL-encoded name
    app.logger.info(f"Accessing subtitles for movie: {name}")  # Debugging
    movie = next((m for m in movies if m['name'] == name), None)
    if not movie:
        app.logger.error(f"Movie not found: {name}")
        return 'Movie not found', 404
    if not movie['has_srt']:
        app.logger.warning(f"No subtitles for movie: {name}")
        return 'No subtitles available', 400
    try:
        app.logger.info(f"Attempting to open SRT file: {movie['srt']}")  # Log the exact path
        with open(movie['srt'], 'r', encoding='utf-8', errors='ignore') as f:
            subs = list(srt.parse(f))
        # Convert timedelta to SRT-compatible strings
        for sub in subs:
            sub.start_str = timedelta_to_srt(sub.start)
            sub.end_str = timedelta_to_srt(sub.end)
        session['movie'] = movie['video']
        return render_template('subtitles.html', subs=subs, name=name)
    except FileNotFoundError as e:
        app.logger.error(f"SRT file not found at {movie['srt']}: {str(e)}")
        return f'SRT file not found: {movie["srt"]}', 500
    except Exception as e:
        app.logger.error(f"Error reading SRT for {name}: {str(e)}")
        return 'Error reading subtitles', 500

@app.route('/output', methods=['GET', 'POST'])
def output():
    if request.method == 'POST':
        start_str = request.form['start']
        end_str = request.form['end']
        session['start'] = start_str
        session['end'] = end_str
        app.logger.info(f"Output page: Start={start_str}, End={end_str}")
    start = session.get('start')
    end = session.get('end')
    video = session.get('movie')
    if not all([start, end, video]):
        app.logger.warning("Missing session data, redirecting to index")
        return redirect(url_for('index'))
    res = get_resolution(video)
    app.logger.info(f"Using resolution for {video}: {res[0]}x{res[1]}")  # Debug resolution
    return render_template('output.html', original_res=res)

@app.route('/generate', methods=['POST'])
def generate():
    app.logger.info("Entered /generate route")  # Debug entry point
    padding = int(request.form.get('padding', 0))
    format = request.form.get('format', 'mp4')
    scale_factor = float(request.form.get('scale', 1.0))
    start_str = session.get('start')
    end_str = session.get('end')
    app.logger.info(f"Selected start time: {start_str}")
    app.logger.info(f"Selected end time: {end_str}")
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
    video = session['movie']
    output_file = os.path.join(TMP_DIR, f"{uuid.uuid4()}.{format}")
    res = get_resolution(video)
    w, h = int(res[0] * scale_factor), int(res[1] * scale_factor)
    scale_filter = f'scale={res[0]}:{res[1]}' if scale_factor == 1.0 else f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
    cmd = [
        'ffmpeg', '-ss', str(start_sec), '-i', video, '-t', str(duration),
        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k', '-ac', '2',
        '-threads', '4', '-r', '23.98', '-vf', scale_filter, '-probesize', '10000000', '-analyzeduration', '10000000', output_file
    ]
    app.logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
    process = subprocess.run(cmd, capture_output=True, text=True)
    app.logger.info(f"FFmpeg stdout: {process.stdout}")
    app.logger.error(f"FFmpeg stderr: {process.stderr}")
    if process.returncode != 0:
        app.logger.error(f"FFmpeg failed with return code {process.returncode}")
    # Verify output file
    if os.path.exists(output_file):
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', output_file]
        try:
            duration_out = subprocess.check_output(probe_cmd, text=True).strip()
            app.logger.info(f"Output file duration: {duration_out} seconds")
            if float(duration_out) == 0:
                app.logger.error(f"Output file {output_file} has zero duration")
        except Exception as e:
            app.logger.error(f"Failed to probe output file {output_file}: {str(e)}")
    else:
        app.logger.error(f"Output file {output_file} was not created")
    session['output'] = output_file
    return redirect(url_for('preview'))

@app.route('/preview')
def preview():
    output = session.get('output')
    if not output:
        app.logger.warning("No output file in session, redirecting to index")
        return redirect(url_for('index'))
    format = output.split('.')[-1]
    return render_template('preview.html', file=output, format=format)

@app.route('/history')
def history():
    output = session.get('output')
    if not output:
        app.logger.warning("No output file in session, redirecting to index")
        return redirect(url_for('index'))
    format = output.split('.')[-1]
    return render_template('history.html', file=output, format=format)

@app.route('/download')
def download():
    output = session.get('output')
    if not output:
        app.logger.warning("No output file to download, redirecting to index")
        return redirect(url_for('index'))
    try:
        return send_file(output, as_attachment=True)
    finally:
        os.remove(output)
        session.pop('output', None)

@app.route('/resolution')
def resolution():
    video = session.get('movie')
    scale = float(request.args.get('scale', 1.0))
    res = get_resolution(video)
    w, h = int(res[0] * scale), int(res[1] * scale)
    return jsonify({'scaled': f'{w}x{h}'})

def timedelta_from_str(time_str):
    try:
        time_str = time_str.split(',')[0] + '.' + time_str.split(',')[1] if ',' in time_str else time_str
        h, m, s = map(float, time_str.split(':'))
        return timedelta(hours=h, minutes=m, seconds=s)
    except Exception as e:
        app.logger.error(f"Error in timedelta_from_str for {time_str}: {str(e)}")
        return timedelta(seconds=0)

@app.route('/serve')
def serve():
    file = request.args.get('file')
    app.logger.info(f"Attempting to serve file: {file}")  # Debug attempt
    if file and os.path.exists(file):
        mime_type = 'video/mp4' if file.endswith('.mp4') else 'audio/mp3'
        app.logger.info(f"Serving file: {file} with MIME type: {mime_type}")
        try:
            response = send_file(file, mimetype=mime_type, as_attachment=False)
            response.headers['Accept-Ranges'] = 'bytes'  # Enable byte-range requests for streaming
            response.headers['Content-Disposition'] = 'inline'  # Ensure inline playback
            response.headers['Access-Control-Allow-Origin'] = '*'  # Allow CORS for testing
            response.headers['Content-Type'] = mime_type  # Explicitly set Content-Type
            response.headers['Cache-Control'] = 'no-cache'  # Prevent caching issues
            return response
        except Exception as e:
            app.logger.error(f"Failed to serve file {file}: {str(e)}")
            return 'Error serving file', 500
    app.logger.error(f"File not found for serving: {file}")
    return 'Not found', 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)  # Enable debug mode