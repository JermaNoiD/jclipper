# jclipper
Dockerized web app for easily making clips from your library of movies using .srt subtitle files. 

## Pre-requisites
- Docker and Docker Compose installed

## Installation
Save the Docker Compose below to a "docker-compose.yml" file
```
services:
  jclipper:
    container_name: jclipper
    environment:
      - MOVIES_DIR=/movies
      - TMP_DIR=/output
      - VIDEO_EXTENSIONS=mp4,mkv,avi,mov,wmv,flv
    image: jclipper
    ports:
      - "5000:5000"
    restart: unless-stopped
    volumes:
      - /path/to/movies:/movies  #Movies directory. This should match the MOVIES_DIR environment variable.
      - /path/to/output_clips:/output #Output directory. This should match the TMP_DIR environment variable. 
```
Modify the movies volume to your movies directory
Modify the output volume to your output directory

Within the same directory, run ```docker compose up -d```

## Usage
When running this docker compose as is, it will be accessible at http://[server IP]:5000

#### Select your movie
The home page should show a list of your movie files if they've been mapped correctly. 
Movies that don't contain a matching .srt file will be colored red. The .srt file name must match the movie file name, not including the extension.
Select a movie to proceed to the subtitle page.

#### Select the the time stamps for your clip
This will display the .srt file for the movie.
Search for the quote to navigate directly to that part of the .srt file. 
- The first click sets the start point of the clip.
- The second click sets the end point of the clip.
Click proceed to continue to the output settings page.

#### Output Settings
If you need to add some seconds to the beginning and end of the clip, this can be done by adding some seconds to the padding field. 
Scale factor allows you to scale the resolution of the output clip.
Format allows you to select mp4 for audio/video or mp3 for just audio.
Click Generate to create the clip. For 4k files this may take a minute depending on how large the clip is. 

#### Output Page
THis should show you a live preview of your video as well as the ability to download it, modify the settings, or start over from the beginning. I'm currently working on an issue with Vivaldi not showing the preview for larger files, but this seems to work fine in other browsers like Chrome

### Roadmap
- Add support for multiple movie libraries
- Export directly to a discord webhook
- Add gpu support



