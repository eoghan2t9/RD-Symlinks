MORE IN DEPTH README TO COME BUT ALL YOU NEED ARE THE FOLLOWING:

MOVIES AND SERIES SYMLINK CREATOR

PYTHON 3 INSTALLED 
THESE PYTHON MODULES INSTALLED: RICH, FABULOUS, IMDBPY AND GUESSIT

```
pip install guessit rich fabulous IMDbPY
```

EDIT THE TOP PART OF THE SCRIPT TO MEDIA LOCATIONS I.E.:

```
# Determine the paths based on the operating system
if os.name == 'posix':  # Linux
    MOVIES_WATCH_DIRECTORY = os.getenv('MOVIES_WATCH_DIRECTORY', "/mnt/remote/realdebrid/movies")
    MOVIES_TARGET_DIRECTORY = os.getenv('MOVIES_TARGET_DIRECTORY', "/media-files/Movies/")
    SERIES_WATCH_DIRECTORY = os.getenv('SERIES_WATCH_DIRECTORY', "/mnt/remote/realdebrid/shows")
    SERIES_TARGET_DIRECTORY = os.getenv('SERIES_TARGET_DIRECTORY', "/media-files/TV-Shows")
    WORKING_DIRECTORY = os.getenv('WORKING_DIRECTORY', "/path/to")
elif os.name == 'nt':  # Windows
    MOVIES_WATCH_DIRECTORY = os.getenv('MOVIES_WATCH_DIRECTORY', r"E:\movies")
    MOVIES_TARGET_DIRECTORY = os.getenv('MOVIES_TARGET_DIRECTORY', r"C:\test")
    SERIES_WATCH_DIRECTORY = os.getenv('SERIES_WATCH_DIRECTORY', r"E:\shows")
    SERIES_TARGET_DIRECTORY = os.getenv('SERIES_TARGET_DIRECTORY', r"C:\test")
    WORKING_DIRECTORY = os.getenv('WORKING_DIRECTORY', r"C:\path\to")
```

THEN RUN THE SCRIPT LIKE

```
python3 Cinesync.py
```

THE SCRIPT CONTAINS A MAIN MENU FOR THE OPTIONS INCLUDED AND ALSO SWITCHES TO MANUALLY RUN CERTAIN PARTS

```
python3 Cinesync.py --watch #Run watcher mode
python3 Cinesync.py --setup #Run first-time setup
python3 Cinesync.py --service #Setup watcher to run at boot
```

WARNING THIS PROCESS WILL TAKE SOME TIME AS THIS SCRIPT NOT ONLY SYMLINKS THE FILES IT ALSO RENAMES THE FILE BETTER FOR THE LIKES OF PLEX AND ALSO APPENDS THE IMDB ID TO THE FOLDER THE SYMLINK IS STORED IN E.G. Batman Begins (2005) {imdb-tt0372784}
