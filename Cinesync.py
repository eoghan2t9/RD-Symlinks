import os
import sys
import json
import subprocess
import argparse
from time import sleep

# Add this line to print sys.path
#print("Python Path:", sys.path)

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rich.console import Console
from rich.logging import RichHandler
import logging
from guessit import guessit
from imdb import IMDb

# Initialize Rich logging
console = Console()
logger = logging.getLogger(__name__)
logger.addHandler(RichHandler(console=console, markup=True))
logger.setLevel(logging.INFO)

# Path to the directory containing this script
script_directory = os.path.dirname(os.path.abspath(__file__))

# Path to Cinesync.py (adjust as needed)
script_path = os.path.join(script_directory, "Cinesync.py")

# Determine the paths based on the operating system
if os.name == 'posix':  # Linux
    MOVIES_WATCH_DIRECTORY = os.getenv('MOVIES_WATCH_DIRECTORY', "YOUR-PATH-TO-YOUR-RD-MOVIES-FOLDER")
    MOVIES_TARGET_DIRECTORY = os.getenv('MOVIES_TARGET_DIRECTORY', "YOUR-PATH-TO-YOUR-LOCAL-MOVIES-FOLDER")
    SERIES_WATCH_DIRECTORY = os.getenv('SERIES_WATCH_DIRECTORY', "YOUR-PATH-TO-YOUR-RD-SERIES-FOLDER")
    SERIES_TARGET_DIRECTORY = os.getenv('SERIES_TARGET_DIRECTORY', "YOUR-PATH-TO-YOUR-LOCAL-MOVIES-FOLDER")
    WORKING_DIRECTORY = os.getenv('WORKING_DIRECTORY', "YOUR-PATH-TO-THIS-FOLDER-WITH-THE-CINESYNC.PY-FILE-INIT")
elif os.name == 'nt':  # Windows
    MOVIES_WATCH_DIRECTORY = os.getenv('MOVIES_WATCH_DIRECTORY', r"E:\movies")
    MOVIES_TARGET_DIRECTORY = os.getenv('MOVIES_TARGET_DIRECTORY', r"C:\test")
    SERIES_WATCH_DIRECTORY = os.getenv('SERIES_WATCH_DIRECTORY', r"E:\shows")
    SERIES_TARGET_DIRECTORY = os.getenv('SERIES_TARGET_DIRECTORY', r"C:\test")
    WORKING_DIRECTORY = os.getenv('WORKING_DIRECTORY', r"C:\YOUR-PATH-TO-THIS-FOLDER-WITH-THE-CINESYNC.PY-FILE-INIT")
else:
    raise NotImplementedError("Unsupported operating system")

# Normalize paths and convert to absolute paths
MOVIES_WATCH_DIRECTORY = os.path.abspath(os.path.normpath(MOVIES_WATCH_DIRECTORY))
MOVIES_TARGET_DIRECTORY = os.path.abspath(os.path.normpath(MOVIES_TARGET_DIRECTORY))
SERIES_WATCH_DIRECTORY = os.path.abspath(os.path.normpath(SERIES_WATCH_DIRECTORY))
SERIES_TARGET_DIRECTORY = os.path.abspath(os.path.normpath(SERIES_TARGET_DIRECTORY))
WORKING_DIRECTORY = os.path.abspath(os.path.normpath(WORKING_DIRECTORY))

logger.info("Movies Watch Directory: {}".format(MOVIES_WATCH_DIRECTORY))
logger.info("Movies Target Directory: {}".format(MOVIES_TARGET_DIRECTORY))
logger.info("Series Watch Directory: {}".format(SERIES_WATCH_DIRECTORY))
logger.info("Series Target Directory: {}".format(SERIES_TARGET_DIRECTORY))
logger.info("Working Directory: {}".format(WORKING_DIRECTORY))

class Handler(FileSystemEventHandler):
    def __init__(self, movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory):
        self.movies_watch_directory = movies_watch_directory
        self.movies_target_directory = movies_target_directory
        self.series_watch_directory = series_watch_directory
        self.series_target_directory = series_target_directory
        self.imdb = IMDb()
        self.symlink_map_file = os.path.join(movies_target_directory, 'symlink_map.json')
        self.load_symlink_map()

    def load_symlink_map(self):
        if os.path.exists(self.symlink_map_file):
            with open(self.symlink_map_file, 'r') as f:
                self.symlink_map = json.load(f)
        else:
            self.symlink_map = {}

    def save_symlink_map(self):
        with open(self.symlink_map_file, 'w') as f:
            json.dump(self.symlink_map, f, indent=4)

    def on_created(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    def process(self, file_path):
        if file_path.endswith(('.mp4', '.mkv')):
            try:
                file_info = guessit(file_path)
                title = file_info.get('title')
                file_type = file_info.get('type')
                imdb_id = self.get_imdb_id(title)

                if file_path.startswith(self.movies_watch_directory):
                    if file_type == 'movie':
                        self.process_movie(file_path, file_info, imdb_id)
                elif file_path.startswith(self.series_watch_directory):
                    if file_type == 'episode':
                        self.process_series(file_path, file_info, imdb_id)
                else:
                    logger.warning("Unknown file type for {}".format(file_path))
            except Exception as e:
                logger.error("Error processing file: {}".format(e))

    def process_movie(self, file_path, file_info, imdb_id):
        title = file_info.get('title')
        year = file_info.get('year')

        if not title:
            logger.warning(f"Missing title for movie processing: {file_path}")
            return

        # Use year in folder name if available, otherwise use title only
        movie_dir_name = f"{title} ({year})" if year else title
        movie_dir = os.path.join(self.movies_target_directory, movie_dir_name)

        # Check if a file already exists in the movie directory
        if not os.path.exists(movie_dir) or not os.listdir(movie_dir):
            os.makedirs(movie_dir, exist_ok=True)

            movie_file_name = f"{title}{os.path.splitext(file_path)[1]}"
            symlink_path = os.path.join(movie_dir, movie_file_name)

            try:
                os.symlink(file_path, symlink_path)
            except OSError as e:
                logger.error(f"Failed to create symlink for {file_path}: {e}")
            else:
                self.symlink_map[file_path] = symlink_path
                self.save_symlink_map()
                logger.info(f"Symlink created: {symlink_path}")

            # Add IMDb ID and year in curly braces to the movie folder name if found
            if imdb_id != "N/A" and year:
                formatted_imdb_id = f"imdb-tt{imdb_id}"
                new_movie_dir = os.path.join(
                    self.movies_target_directory, f"{title} ({year}) {{{formatted_imdb_id}}}"
                )
                os.rename(movie_dir, new_movie_dir)
                logger.info(f"Renamed movies folder: {movie_dir} to {new_movie_dir}")
        else:
            logger.info(
                f"Skipping symlink creation for {file_path} as the directory {movie_dir} is not empty."
            )

    def process_series(self, file_path, file_info, imdb_id):
        series_title = file_info.get("title") or file_info.get("series")
        season_number = file_info.get("season")
        episode_number = file_info.get("episode")
        year = file_info.get("year")

        if not all([series_title, season_number, episode_number]):
            logger.warning(f"Missing information for series processing: {file_path}")
            return

        # Use year in series folder name if available
        series_dir_name = f"{series_title} ({year})" if year else series_title
        series_dir = os.path.join(self.series_target_directory, series_dir_name)
        season_dir = os.path.join(series_dir, f"Season {season_number:02d}")

        # Check if a file already exists in the season directory
        if not os.path.exists(season_dir) or not os.listdir(season_dir):
            os.makedirs(season_dir, exist_ok=True)

            series_file_name = f"{series_title} - S{season_number:02d}E{episode_number:02d}{os.path.splitext(file_path)[1]}"
            symlink_path = os.path.join(season_dir, series_file_name)

            try:
                os.symlink(file_path, symlink_path)
            except OSError as e:
                logger.error(f"Failed to create symlink for {file_path}: {e}")
            else:
                self.symlink_map[file_path] = symlink_path
                self.save_symlink_map()
                logger.info(f"Symlink created: {symlink_path}")

            # Add IMDb ID and year in curly braces to the series folder name if found
            if imdb_id != "N/A" and year:
                formatted_imdb_id = f"imdb-tt{imdb_id}"
                new_series_dir = os.path.join(
                    self.series_target_directory, f"{series_title} ({year}) {{{formatted_imdb_id}}}"
                )
                os.rename(series_dir, new_series_dir)
                logger.info(f"Renamed series folder: {series_dir} to {new_series_dir}")
        else:
            logger.info(
                f"Skipping symlink creation for {file_path} as the directory {season_dir} is not empty."
            )

    def get_imdb_id(self, title):
        try:
            results = self.imdb.search_movie(title)
            if results:
                return results[0].movieID
            else:
                logger.warning(f"No IMDb ID found for title: {title}")
                return "N/A"
        except Exception as e:
            logger.error(f"Error fetching IMDb ID: {e}")
            return "N/A"

def run_watcher(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory):
    event_handler = Handler(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory)
    observer = Observer()
    observer.schedule(event_handler, movies_watch_directory, recursive=True)
    observer.schedule(event_handler, series_watch_directory, recursive=True)
    observer.start()

    try:
        logger.info("Watching for new files...")
        while True:
            sleep(1)  # Add a sleep to prevent high CPU usage
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def run_first_time_setup(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory):
    logger.info("Running first-time setup...")

    if not os.path.exists(movies_watch_directory):
        logger.error("Movies watch directory does not exist: {}".format(movies_watch_directory))
        return

    if not os.path.exists(series_watch_directory):
        logger.error("Series watch directory does not exist: {}".format(series_watch_directory))
        return

    handler = Handler(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory)

    logger.info("Processing files in movies watch directory: {}".format(movies_watch_directory))
    for subdir, _, files in os.walk(movies_watch_directory):
        for filename in files:
            file_path = os.path.join(subdir, filename)
            if os.path.isfile(file_path):
                logger.info("Processing file: {}".format(file_path))
                handler.process(file_path)

    logger.info("Processing files in series watch directory: {}".format(series_watch_directory))
    for subdir, _, files in os.walk(series_watch_directory):
        for filename in files:
            file_path = os.path.join(subdir, filename)
            if os.path.isfile(file_path):
                logger.info("Processing file: {}".format(file_path))
                handler.process(file_path)

    logger.info("First-time setup completed.")

def setup_service():
    print("Choose the service setup:")
    print("1. Systemd (Linux)")
    print("2. Windows")
    choice = input("Enter your choice: ")

    if choice == "1":
        # Setup systemd service
        setup_systemd_service()
    elif choice == "2":
        # Setup Windows service
        setup_windows_service()
    else:
        print("Invalid choice. Please enter '1' or '2'.")

def setup_systemd_service():
    # Write systemd service file
    service_content = """
[Unit]
Description=File Watcher Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 {script_path} --watch
WorkingDirectory={working_directory}
StandardOutput=syslog
StandardError=syslog
Restart=always

[Install]
WantedBy=multi-user.target
""".format(script_path=script_path, working_directory=WORKING_DIRECTORY)
    
    with open('/etc/systemd/system/file_watcher.service', 'w') as f:
        f.write(service_content)

    # Enable and start the service
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'])
    subprocess.run(['sudo', 'systemctl', 'enable', 'file_watcher.service'])
    subprocess.run(['sudo', 'systemctl', 'start', 'file_watcher.service'])
    print("Systemd service setup complete.")

def setup_windows_service():
    # Path to the batch script that runs the Python script
    batch_script_path = os.path.abspath("CineSync.bat")
    
    # Create the batch script content
    batch_script_content = """
@echo off
cd /d %~dp0
python {script_path} --watch
""".format(script_path=script_path)

    # Write the batch script to a file
    with open(batch_script_path, "w") as batch_file:
        batch_file.write(batch_script_content)

    # Install the service using SC command
    service_name = "FileWatcherService"
    service_exe_path = os.path.abspath("CineSync.bat")
    sc_create_command = 'sc create {service_name} binPath= "{service_exe_path}" start= auto'.format(service_name=service_name, service_exe_path=service_exe_path)
    
    # Start the service
    sc_start_command = 'sc start {service_name}'.format(service_name=service_name)

    # Execute SC commands
    subprocess.run(sc_create_command, shell=True)
    subprocess.run(sc_start_command, shell=True)

    print("Windows service setup complete.")

def main():
    parser = argparse.ArgumentParser(description="File Watcher and Organizer")
    parser.add_argument('--watch', action='store_true', help='Run watcher mode')
    parser.add_argument('--setup', action='store_true', help='Run first-time setup')
    parser.add_argument('--service', action='store_true', help='Setup service')
    
    args = parser.parse_args()
    
    if args.watch:
        run_watcher(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
    elif args.setup:
        run_first_time_setup(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
    elif args.service:
        setup_service()
    else:
        print(r"""

    a88888b. oo                   .d88888b
   d8'   `88                      88.    "'
   88        dP 88d888b. .d8888b. `Y88888b. dP    dP 88d888b. .d8888b.
   88        88 88'  `88 88ooood8       `8b 88    88 88'  `88 88'  `"`
   Y8.   .88 88 88    88 88.  ... d8'   .8P 88.  .88 88    88 88.  ...
    Y88888P' dP dP    dP `88888P'  Y88888P  `8888P88 dP    dP `88888P'
                                                 .88
                                             d8888P


                """)
        print("Welcome to the script main menu:")
        print("1. Perform first-time setup")
        print("2. Run watcher")
        print("3. Setup service")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            run_first_time_setup(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
        elif choice == "2":
            run_watcher(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
        elif choice == "3":
            setup_service()
        elif choice == "4":
            print("Quitting Script")
            sys.exit(1)
        else:
            print("Invalid choice. Please enter '1', '2', or '3'.")
            sys.exit(1)

if __name__ == '__main__':
    main()
