# Music for reels

Background tracks by Kevin MacLeod (incompetech.com), licensed under Creative Commons Attribution 4.0
(https://creativecommons.org/licenses/by/4.0/). Every reel caption credits the track it uses
(`scripts/publish.py` adds the line from `tracks.json`).

`scripts/reel.py` uses the tracks in turn (by the number of reels already in `data/posted.json`),
starting at `start` seconds, mixed quietly under the narration. To add a track, drop the MP3 here and
add an entry to `tracks.json` with its title, artist, and license.
