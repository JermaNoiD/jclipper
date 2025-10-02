# jclipper
Dockerized web app for easily making clips from your library of movies using .srt subtitle files. 

## Pre-requisites
- Docker and Docker Compose installed

## Installation
Create a new docker-compose.yml file containing the following compose block
Modify the /movies and /output volume mounts
Within the same directory, run ```docker compose up -d```
When running this docker compose as is, it will be accessible at http://[server IP]:5000
```
services:
  jclipper:
    container_name: jclipper
    environment:
      - MOVIES_DIR=/movies #Internal docker directory, no need to actually change this. Just map your volume path to this directory
      - OUTPUT_DIR=/output #Internal docker directory, no need to actually change this. Just map your volume path to this directory
      - VIDEO_EXTENSIONS=mp4,mkv,avi,mov,wmv,flv #Video extensions to scan for
      - SECRET_KEY=secret #Session secret. Set this to something random.
      - PREVIEW_RESOLUTION=1280x720
      - DEFAULT_LANGUAGE=en
      - S3_ENDPOINT= #provide all S3 fields to enable the S3 upload button
      - S3_REGION=
      - S3_BUCKET=
      - S3_KEY=
      - S3_SECRET=
      - S3_LINK_FORMAT= #presigned or basic. presigned is required if you are using Garage as your S3 provider, as it does not have the ability for anonymous access. But basic links are prettier
      - FFMPEG_LOG_ENABLED=false #For seeing console log output of FFMPEG, final output log is already shown on the preview page.
      - STARTUP_SCAN_LOG_ENABLED=false #For seeing console log output of the directory scan process
    image: jermanoid/jclipper:latest
    ports:
      - "5000:5000"
    restart: unless-stopped
    volumes:
      - /path/to/movies:/movies:ro  #Movies directory
      - /path/to/output_clips:/output #Output directory
```

<img width="967" height="846" alt="image" src="https://github.com/user-attachments/assets/6cf9f2a2-63d9-47a5-acbe-0f4976bca178" />

#### Select your movie
The home page should show a list of your movie files if they've been mapped correctly. The app has been programmed to search recursively through the /movies folder for the common extensions listed in the VIDEO_EXTENSIONS environment variable.
Select a valid movie to proceed to the subtitle page.

Movies that don't contain a matching .srt file in the same movie folder will be colored greyed out, and present a red "subtitle file not found" tag. The .srt file name must match the movie file name, not including the extension or language signifier. (e.g. "en.srt" or "fr.srt")
I have a library that was set up for Plex, and so I built this app around that type of library organization.


#### Select the the time stamps for your clip
This will display the .srt file for the movie.
Search for the quote to navigate directly to that part of the .srt file, or scroll to it. 
- The first click sets the start point of the clip.
- The second click sets the end point of the clip
- Click the clear button, to clear your markers
Click proceed to continue to the output settings page.

<img width="958" height="870" alt="image" src="https://github.com/user-attachments/assets/13814266-312e-4c12-a8e1-10edebb73ae0" />


#### Output Settings
If you need to add some seconds of time flanking the clip to capture a certain moment outside the .srt timestamp, this can be done by adding some seconds to the padding field. 
Scale factor allows you to scale the resolution of the output clip down from the native file resolution.
Format allows you to select:
- mp4,mkv,avi for audio/video
- gif,avif for video only
- mp3 for audio only.

Click Generate to create the clip. For 4k files this may take a minute depending on how large the clip is, but a quick 720p preview clip will be generated

<img width="961" height="502" alt="image" src="https://github.com/user-attachments/assets/fe6e4e88-0fe1-4826-bdeb-447ead7c369d" />


#### Preview Page
This should show you a live preview of your video as well as the ability to download it, Upload to S3, modify the settings (deletes the existing clip), or cancel back to the movie page (Also deletes the existing clip). 

<img width="953" height="753" alt="image" src="https://github.com/user-attachments/assets/282fad7d-affe-4002-92fb-9bab252ea64b" />


### Roadmap
- Add GPU encoding support
- Interface for managing clips stored in your S3 bucket.

### Known Issues


