# jclipper
Dockerized web app for easily making clips from your library of movies using .srt subtitle files. 

## Pre-requisites
- Docker and Docker Compose installed

## Installation

Using Docker Compose:

1. Copy the sample config and edit it for your setup:
   ```bash
   cp .env.sample .env
   ```
   Set at least `SECRET_KEY` (a long random string). Optionally set the `S3_*` values to enable uploads.

2. Copy the sample compose file and set your volume paths:
   ```bash
   cp docker-compose-sample.yml docker-compose.yml
   ```
   Point the `volumes` at your host directories.

3. Start the container:
   ```bash
   docker compose up -d
   ```
   The app will be accessible at `http://[server IP]:5000`.

All configuration lives in `.env` (loaded via `env_file`). Key values:
- `MOVIES_DIR`, `TV_SHOWS_DIR`, `OUTPUT_DIR` — paths inside the container; map your host dirs to these in the compose `volumes`
- `SECRET_KEY` — required, set to a long random string
- `S3_*` — enable S3 upload (leave blank to disable); `S3_LINK_FORMAT` is `basic` or `presigned` (use `presigned` for S3-compatible stores without anonymous read access, e.g. Garage)
- `EXCLUDED_FOLDERS` — comma-separated folder names to skip when scanning (e.g. `@eaDir` on Synology)
- `FFMPEG_LOG_ENABLED`, `STARTUP_SCAN_LOG_ENABLED` — set to `false` to reduce console noise

#### Select Movie or TV show workflow from the home page

#### Select your movie or tv show season>episode
The home page should show a list of your movie files if they've been mapped correctly. The app has been programmed to search recursively through the /movies folder for the common extensions listed in the VIDEO_EXTENSIONS environment variable. Any folders listed in the EXCLUDED_FOLDERS environment variable are skipped during the scan, which is useful for hiding NAS metadata folders (e.g. @eaDir on Synology) that show up in your library.
Select a valid movie to proceed to the subtitle page.

Movies that don't contain a matching .srt file in the same movie folder will be colored greyed out, and present a red "subtitle file not found" tag. The .srt file name must match the movie file name, not including the extension or language signifier. (e.g. "en.srt" or "fr.srt")
I have a library that was set up for Plex, and so I built this app around that type of library organization.

<img width="400" alt="image" src="https://github.com/user-attachments/assets/6cf9f2a2-63d9-47a5-acbe-0f4976bca178" />

#### Select the the time stamps for your clip
This will display the .srt file for the movie.
Search for the quote to navigate directly to that part of the .srt file, or scroll to it. 
- The first click sets the start point of the clip.
- The second click sets the end point of the clip
- Click the clear button, to clear your markers
Click proceed to continue to the output settings page.

<img width="400" alt="image" src="https://github.com/user-attachments/assets/13814266-312e-4c12-a8e1-10edebb73ae0" />


#### Output Settings
If you need to add some seconds of time flanking the clip to capture a certain moment outside the .srt timestamp, this can be done by adding some seconds to the padding field. 
Scale factor allows you to scale the resolution of the output clip down from the native file resolution.
Format allows you to select:
- mp4 for audio/video
- gif for video only
- mp3 for audio only.

Click Generate to create the clip. For 4k files this may take a minute depending on how large the clip is

<img width="400" alt="image" src="https://github.com/user-attachments/assets/fe6e4e88-0fe1-4826-bdeb-447ead7c369d" />


#### Preview Page
This should show you a live preview of your video as well as the ability to download it, Upload to S3, modify the settings (deletes the existing clip), or cancel back to the movie page (Also deletes the existing clip). 

<img width="400" alt="image" src="https://github.com/user-attachments/assets/282fad7d-affe-4002-92fb-9bab252ea64b" />


### Roadmap
- Add GPU encoding support for final clip output (preview encoding is already GPU-accelerated)
- Interface for managing clips stored in your S3 bucket.

### Known Issues


