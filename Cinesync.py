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
import tmdbsimple as tmdb
import re
import concurrent.futures

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
    MOVIES_WATCH_DIRECTORY = os.getenv('MOVIES_WATCH_DIRECTORY', "/mnt/empty")
    MOVIES_TARGET_DIRECTORY = os.getenv('MOVIES_TARGET_DIRECTORY', "/media-files/Movies")
    SERIES_WATCH_DIRECTORY = os.getenv('SERIES_WATCH_DIRECTORY', "/mnt/remote/realdebrid/shows/")
    SERIES_TARGET_DIRECTORY = os.getenv('SERIES_TARGET_DIRECTORY', "/media-files/TV-Shows")
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
                # Clean the filename and containing directory name
                cleaned_file_name = self.clean_file_name(os.path.basename(file_path))
                cleaned_dir_name = self.clean_file_name(os.path.basename(os.path.dirname(file_path)))
                cleaned_file_path = os.path.join(os.path.dirname(file_path), cleaned_file_name)

                file_info = guessit(cleaned_file_path) 

                original_title = file_info.get('title')
                if file_path.startswith(self.movies_watch_directory):
                    file_type = 'movie'
                    id_from_api = self.get_tmdb_id(original_title)

                    # Special character removal only for movies (NEW)
                    if id_from_api == "N/A":
                        special_chars = ['#', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
                        special_chars_tuple = tuple(special_chars)
                        while file_info.get('title', '').startswith(special_chars_tuple):
                            file_info['title'] = file_info['title'][1:]

                        id_from_api = self.get_tmdb_id(file_info.get('title'))
                elif file_path.startswith(self.series_watch_directory):
                    file_type = 'episode'
                    id_from_api = self.get_tmdb_id(original_title, is_movie=False)
                else:
                    logger.warning("Unknown file type for {}".format(file_path))
                    return

                # Multi-Threading Implementation
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_to_file_path = {}

                    if file_type == 'movie':
                        future_to_file_path[executor.submit(self.process_movie, file_path, file_info, id_from_api)] = file_path
                    elif file_type == 'episode':
                        future_to_file_path[executor.submit(self.process_series, file_path, file_info, id_from_api)] = file_path

                    for future in concurrent.futures.as_completed(future_to_file_path):
                        file_path = future_to_file_path[future]
                        try:
                            future.result()  # Get the result or raise an exception if any occurred
                        except Exception as e:
                            logger.error(f"Error processing file {file_path}: {e}")

            except Exception as e:
                logger.error("Error processing file: %s", file_path, exc_info=True)
                
                
    def clean_file_name(self, name):
        words_to_remove = ["TEPES", "rartv", "1080p", "720p", "x264", "x265", "WEB-DL", "BluRay", "BRRip", "WEBRip"]  # Add more as needed
        for word in words_to_remove:
            name = name.replace(word, "", 1)  # Remove the first occurrence only
        
        # Remove trailing dot and hyphen
        name = name.rstrip(".- ")

        # Remove anything enclosed in square brackets
        name = re.sub(r"\[.*?\]", "", name) 

        return name.strip()


    def get_tmdb_id(self, title, is_movie=True):
        try:
            tmdb.API_KEY = '**************************'
            search = tmdb.Search()

            if is_movie:
                query = title.split("(")[0].strip()
                response = search.movie(query=query)
            else:
                # Handle potential colon after numbers in series titles
                if title[0].isdigit() and ':' in title:
                    # If a colon is found, split title and take the second part.
                    query = title.split(':', 1)[1].strip()
                else:
                    query = title  # Use the original title if no colon after numbers

                response = search.tv(query=query)

            if response['results']:
                best_match = max(response['results'], key=lambda x: self.similarity(x['title' if is_movie else 'name'], title))
                return best_match['id']

            logger.warning(f"No TMDb ID found for {'movie' if is_movie else 'series'}: {title}")
            return "N/A"
        except Exception as e:
            logger.error(f"Error fetching TMDb ID: {e}")
            return "N/A"

    def similarity(self, a, b):
        # Simple string similarity check (you could use a more sophisticated method if needed)
        return sum(1 for x, y in zip(a, b) if x == y) / max(len(a), len(b))

    def process_movie(self, file_path, file_info, tmdb_id):
        title = file_info.get('title')
        year = file_info.get('year')

        # Check if symlink already exists
        existing_symlink = self.symlink_map.get(file_path)
        if existing_symlink and os.path.islink(existing_symlink):
            logger.info(f"Symlink already exists for {file_path}: {existing_symlink}")
            return

        if not title:
            logger.warning(f"Missing title for movie processing: {file_path}")
            return

        # Construct the movie directory name with year and TMDb ID if found
        formatted_tmdb_id = f"tmdb-{tmdb_id}" if tmdb_id != "N/A" else ""
        movie_dir_name = f"{title} ({year}) {{{formatted_tmdb_id}}}" if year else f"{title} {{{formatted_tmdb_id}}}"
        movie_dir = os.path.join(self.movies_target_directory, movie_dir_name)

        # Check if the movie directory already exists
        if os.path.exists(movie_dir) and os.listdir(movie_dir):
            logger.info(f"Skipping symlink creation for {file_path} as the directory {movie_dir} is not empty.")
            return

        os.makedirs(movie_dir, exist_ok=True)  # Create the movie directory if it doesn't exist
        
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


    def process_series(self, file_path, file_info, tmdb_id):
        series_title = file_info.get("title") or file_info.get("series")
        season_number = file_info.get("season")
        episode_number = file_info.get("episode")

        # Try to extract the year from the title
        try:
            year_match = re.search(r"\((\d{4})\)", series_title)
            year = year_match.group(1) if year_match else None
        except AttributeError:
            year = None

        # Existing symlink check 
        existing_symlink = self.symlink_map.get(file_path)
        if existing_symlink and os.path.islink(existing_symlink):
            logger.info(f"Symlink already exists for {file_path}: {existing_symlink}")
            return

        if not all([series_title, season_number, episode_number]):
            logger.warning(f"Missing information for series processing: {file_path}")
            return

        # Construct series file name before any logic that might return early
        series_file_name = f"{series_title} - S{season_number:02d}E{episode_number:02d}{os.path.splitext(file_path)[1]}"

        # Normalize folder names for comparison (title case) - keep year
        def normalize_dir_name(name):
            year_match = re.search(r"\((\d{4})\)", name)
            year_part = year_match.group(0) if year_match else ""
            name_without_brackets = re.sub(r"[()]", "", name)
            normalized_name = name_without_brackets.title()
            return normalized_name + year_part
        
        normalized_series_dir_name = normalize_dir_name(series_title)

        # Check if an exact match or similar folder exists (case-insensitive)
        existing_series_dirs = [d for d in os.listdir(self.series_target_directory) if normalize_dir_name(d) == normalized_series_dir_name]
        if existing_series_dirs:
            # If the directory already exists, create a new name with a suffix (if duplicates exist)
            counter = 1
            while True:
                new_series_dir_name = f"{series_title} ({year}) ({counter})" if counter > 1 else series_title
                new_series_dir = os.path.join(self.series_target_directory, new_series_dir_name)
                if normalize_dir_name(new_series_dir_name) not in [normalize_dir_name(d) for d in existing_series_dirs]:
                    break
                counter += 1

            logger.warning(f"Series directory already exists. Creating a new directory: {new_series_dir}")
            series_dir = new_series_dir  # Update series_dir
        else:
            # If the directory doesn't exist, create it with the TMDB ID and year
            if tmdb_id != "N/A":
                formatted_tmdb_id = f"tmdb-{tmdb_id}"
                series_dir_name = f"{series_title} ({year}) {{{formatted_tmdb_id}}}" if year else f"{series_title} {{{formatted_tmdb_id}}}"
                series_dir = os.path.join(self.series_target_directory, series_dir_name)

        # Create the series directory
        os.makedirs(series_dir, exist_ok=True)

        season_dir = os.path.join(series_dir, f"Season {season_number:02d}")
        os.makedirs(season_dir, exist_ok=True)  # Create season dir if it doesn't exist
        
        # Check if an exact match exists within the season folder
        existing_season_files = [f for f in os.listdir(season_dir) if normalize_dir_name(f) == normalize_dir_name(series_file_name)]
        if existing_season_files:
            logger.info(f"Skipping symlink creation for {file_path} as a similar file exists in the season directory.")
            return
        
        symlink_path = os.path.join(season_dir, series_file_name)

        try:
            os.symlink(file_path, symlink_path)
        except OSError as e:
            logger.error(f"Failed to create symlink for {file_path}: {e}")
        else:
            self.symlink_map[file_path] = symlink_path
            self.save_symlink_map()
            logger.info(f"Symlink created: {symlink_path}")

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

    # Multi-Threading Implementation
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_file_path = {}

        # Process Movies
        logger.info("Processing files in movies watch directory: {}".format(movies_watch_directory))
        for subdir, _, files in os.walk(movies_watch_directory):
            for filename in files:
                file_path = os.path.join(subdir, filename)
                if os.path.isfile(file_path):
                    future_to_file_path[executor.submit(handler.process, file_path)] = file_path

        # Process Series
        logger.info("Processing files in series watch directory: {}".format(series_watch_directory))
        for subdir, _, files in os.walk(series_watch_directory):
            for filename in files:
                file_path = os.path.join(subdir, filename)
                if os.path.isfile(file_path):
                    future_to_file_path[executor.submit(handler.process, file_path)] = file_path

        # Wait for Completion and Handle Exceptions
        for future in concurrent.futures.as_completed(future_to_file_path):
            file_path = future_to_file_path[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")

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
