import os
import sys
import sqlite3
import subprocess
import argparse
import json
from time import sleep
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from logging import FileHandler, Formatter
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rich.console import Console
from rich.logging import RichHandler
from guessit import guessit
import subliminal
from subliminal import region
import tmdbsimple as tmdb
import re
from tqdm import tqdm
from titlecase import titlecase
import Levenshtein
from functools import lru_cache
from pathlib import Path

# Setting to stop the script on error or warning
stop_on_error = True

# Initialize Rich console
console = Console()

# Setup logging configuration
def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Console handler with Rich
    console_handler = RichHandler(console=console, markup=True)
    console_handler.setLevel(logging.DEBUG)

    # File handlers
    log_directory = Path(__file__).parent / 'logs'
    log_directory.mkdir(parents=True, exist_ok=True)

    processing_log_file = log_directory / 'processing.log'
    warning_log_file = log_directory / 'warnings.log'
    error_log_file = log_directory / 'errors.log'

    processing_handler = FileHandler(processing_log_file)
    processing_handler.setLevel(logging.INFO)
    processing_handler.setFormatter(Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

    warning_handler = FileHandler(warning_log_file)
    warning_handler.setLevel(logging.WARNING)
    warning_handler.setFormatter(Formatter('%(asctime)s - %(level)s - %(message)s\n%(pathname)s:%(lineno)d\n%(message)s\n'))

    error_handler = FileHandler(error_log_file)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(Formatter('%(asctime)s - %(name)s - %(level)s - %(message)s\n%(pathname)s:%(lineno)d\n%(message)s\n'))

    # Adding handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(processing_handler)
    logger.addHandler(warning_handler)
    logger.addHandler(error_handler)

    return logger

# Initialize logger
logger = setup_logging()

# Path to the directory containing this script
script_directory = Path(__file__).parent

# Config directory
config_directory = script_directory / 'config'
config_path = config_directory / 'config.json'

def load_config(config_path):
    if not config_path.exists():
        return {}
    with config_path.open('r') as config_file:
        return json.load(config_file)

def save_config(config, config_path):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open('w') as config_file:
        json.dump(config, config_file, indent=4)

# Load main config
config = load_config(config_path)
tmdb.API_KEY = config.get('TMDB_API_KEY')

# Determine the paths based on the operating system
if os.name == 'posix':  # Linux
    paths = config.get('LINUX_PATHS', {})
elif os.name == 'nt':  # Windows
    paths = config.get('WINDOWS_PATHS', {})
else:
    raise NotImplementedError("Unsupported operating system")

# Get directory paths from config
MOVIES_WATCH_DIRECTORY = Path(paths.get('MOVIES_WATCH_DIRECTORY', '')).resolve()
MOVIES_TARGET_DIRECTORY = Path(paths.get('MOVIES_TARGET_DIRECTORY', '')).resolve()
SERIES_WATCH_DIRECTORY = Path(paths.get('SERIES_WATCH_DIRECTORY', '')).resolve()
SERIES_TARGET_DIRECTORY = Path(paths.get('SERIES_TARGET_DIRECTORY', '')).resolve()
WORKING_DIRECTORY = Path(paths.get('WORKING_DIRECTORY', '')).resolve()

logger.info(f"Movies Watch Directory: {MOVIES_WATCH_DIRECTORY}")
logger.info(f"Movies Target Directory: {MOVIES_TARGET_DIRECTORY}")
logger.info(f"Series Watch Directory: {SERIES_WATCH_DIRECTORY}")
logger.info(f"Series Target Directory: {SERIES_TARGET_DIRECTORY}")
logger.info(f"Working Directory: {WORKING_DIRECTORY}")

class Handler(FileSystemEventHandler):
    def __init__(self, movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory):
        self.movies_watch_directory = movies_watch_directory
        self.movies_target_directory = movies_target_directory
        self.series_watch_directory = series_watch_directory
        self.series_target_directory = series_target_directory

        # Separate databases for movies and series
        db_directory = script_directory / 'db'
        db_directory.mkdir(parents=True, exist_ok=True)

        self.movies_db_path = db_directory / 'movies_symlink_map.db'
        self.series_db_path = db_directory / 'series_symlink_map.db'

        self.db_lock = Lock()

        self.movies_db_conn = sqlite3.connect(self.movies_db_path, check_same_thread=False)
        self.movies_db_cursor = self.movies_db_conn.cursor()

        self.series_db_conn = sqlite3.connect(self.series_db_path, check_same_thread=False)
        self.series_db_cursor = self.series_db_conn.cursor()

        self.init_db()
        self.validate_symlinks()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.tmdb_cache = {}

    def init_db(self):
        with self.movies_db_conn:
            self.movies_db_cursor.execute('''
                CREATE TABLE IF NOT EXISTS symlink_map (
                    file_path TEXT PRIMARY KEY,
                    symlink_path TEXT
                )
            ''')

        with self.series_db_conn:
            self.series_db_cursor.execute('''
                CREATE TABLE IF NOT EXISTS symlink_map (
                    file_path TEXT PRIMARY KEY,
                    symlink_path TEXT
                )
            ''')

        logger.info(f"Initialized movie database at {self.movies_db_path}")
        logger.info(f"Initialized series database at {self.series_db_path}")

    def add_symlink(self, file_path, symlink_path, is_movie=True):
        db_conn = self.movies_db_conn if is_movie else self.series_db_conn
        db_cursor = self.movies_db_cursor if is_movie else self.series_db_cursor

        with self.db_lock:
            db_cursor.execute('REPLACE INTO symlink_map (file_path, symlink_path) VALUES (?, ?)', (str(file_path), str(symlink_path)))
            db_conn.commit()
        
        logger.info(f"Added symlink to {'movies' if is_movie else 'series'} database: {file_path} -> {symlink_path}")

    def get_symlink(self, file_path, is_movie=True):
        db_cursor = self.movies_db_cursor if is_movie else self.series_db_cursor

        with self.db_lock:
            db_cursor.execute('SELECT symlink_path FROM symlink_map WHERE file_path = ?', (str(file_path),))
            result = db_cursor.fetchone()
        
        if result:
            logger.info(f"Found existing symlink in {'movies' if is_movie else 'series'} database: {file_path} -> {result[0]}")
        return Path(result[0]) if result else None

    def remove_symlink(self, file_path, is_movie=True):
        db_conn = self.movies_db_conn if is_movie else self.series_db_conn
        db_cursor = self.movies_db_cursor if is_movie else self.series_db_cursor

        with self.db_lock:
            db_cursor.execute('DELETE FROM symlink_map WHERE file_path = ?', (str(file_path),))
            db_conn.commit()
        
        logger.info(f"Removed symlink from {'movies' if is_movie else 'series'} database: {file_path}")

    def validate_symlinks(self):
        logger.info("Validating symlinks...")

        def validate(db_conn, db_cursor, type_):
            with db_conn:
                db_cursor.execute('SELECT file_path, symlink_path FROM symlink_map')
                rows = db_cursor.fetchall()

            for file_path, symlink_path in rows:
                if not Path(symlink_path).exists() or not Path(symlink_path).is_symlink():
                    logger.warning(f"Invalid symlink detected in {type_} database, removing: {symlink_path}")
                    self.remove_symlink(file_path, is_movie=(type_ == 'movies'))

        validate(self.movies_db_conn, self.movies_db_cursor, 'movies')
        validate(self.series_db_conn, self.series_db_cursor, 'series')

    def on_created(self, event):
        if not event.is_directory:
            logger.info(f"File created: {event.src_path}")
            self.executor.submit(self.process, Path(event.src_path))

    def process(self, file_path):
        logger.info(f"Processing file: {file_path}")
        if file_path.suffix in {'.mp4', '.mkv', '.avi', '.m4v', '.mov'}:
            try:
                if self.is_extras_or_deleted(file_path):
                    logger.info(f"Skipping extras or deleted scenes file: {file_path}")
                    return

                cleaned_file_name = self.clean_file_name(file_path.name)
                preprocessed_file_name = self.preprocess_file_path(file_path)
                cleaned_file_path = file_path.parent / cleaned_file_name

                # Use guessit as primary and subliminal as backup for guessing
                file_info = guessit(preprocessed_file_name)
                if not file_info.get('title'):
                    file_info = self.subliminal_parse(preprocessed_file_name)
                    logger.warning(f"Guessit failed, using Subliminal for {file_path}")

                if not file_info.get('title'):
                    logger.warning(f"Skipping file {file_path} due to missing title information")
                    return
                
                original_title = file_info.get('title')
                
                if file_path.startswith(self.movies_watch_directory):
                    file_type = 'movie'
                    id_from_api = self.get_tmdb_id(original_title, is_movie=True)
                elif file_path.startswith(self.series_watch_directory):
                    file_type = 'episode'
                    id_from_api = self.get_tmdb_id(original_title, is_movie=False)
                else:
                    logger.warning(f"Unknown file type for {file_path}")
                    if stop_on_error:
                        sys.exit(1)
                    return

                if file_type == 'movie':
                    self.process_movie(file_path, file_info, id_from_api)
                elif file_type == 'episode':
                    self.process_series(file_path, file_info, id_from_api)

            except Exception as e:
                logger.error(f"Error processing file: {file_path}", exc_info=True)
                if stop_on_error:
                    sys.exit(1)

    def is_extras_or_deleted(self, file_path):
        # Check for terms like "extras", "deleted scenes", etc.
        extras_keywords = {'extras', 'deleted scenes', 'deleted', 'bonus', 'featurette', 'behind the scenes'}
        file_name = file_path.name.lower()
        return any(keyword in file_name for keyword in extras_keywords)

    def clean_file_name(self, name):
        words_to_remove = {"TEPES", "rartv", "1080p", "720p", "x264", "x265", "WEB-DL", "BluRay", "BRRip", "WEBRip"}
        for word in words_to_remove:
            name = name.replace(word, "", 1)
        
        name = name.rstrip(".- ")
        name = re.sub(r"\[.*?\]", "", name) 

        return name.strip()

    def preprocess_file_path(self, file_path):
        file_name = file_path.name
        
        # Remove known tags and patterns
        patterns_to_remove = [
            r'\[.*?\]', r'\(.*?\)', r'\b(?:1080p|720p|480p|x264|x265|h264|h265|HEVC|HD|SD|BluRay|WEBRip|WEB-DL|HDRip|DVDRip|BRRip|NF|AMZN|HULU|DDP5.1)\b',
            r'\b(?:AAC|AC3|DTS|DD5.1|DDP5.1|MP3|5.1)\b', r'\b(?:YTS|RARBG|EVO|FGT|KiNGS|SMURF|XVID|AMZN|PSA|QOQ|NTb|mkv|mp4|avi|m4v|mov)\b',
            r'[\[\]{}()]', r'\.', r'_+', r'[-]+'
        ]
        for pattern in patterns_to_remove:
            file_name = re.sub(pattern, ' ', file_name, flags=re.IGNORECASE)
        
        # Standardize common formats
        file_name = re.sub(r'\bS(\d{1,2})E(\d{1,2})\b', r'S\1E\2', file_name, flags=re.IGNORECASE)
        file_name = re.sub(r'\b(\d{1,2})x(\d{1,2})\b', r'S\1E\2', file_name, flags=re.IGNORECASE)
        file_name = re.sub(r'\bSeason\s*(\d{1,2})\s*Episode\s*(\d{1,2})\b', r'S\1E\2', file_name, flags=re.IGNORECASE)
        
        # Remove any trailing text after episode pattern
        file_name = re.sub(r'(S\d{1,2}E\d{1,2}).*', r'\1', file_name, flags=re.IGNORECASE)

        file_name = re.sub(r'\s+', ' ', file_name).strip()
        return file_name

    def clean_directory_name(self, name):
        # Remove known tags and patterns
        patterns_to_remove = [
            r'S\d{1,2}.*', r'\.\.\..*', r'\(None\)', r'\(.*\)', r'-RARBG', r'-\[.*?\]', r'\[.*?\]', r'\{.*?\}', r'\s+', r'_+', r'-+'
        ]
        for pattern in patterns_to_remove:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)
        
        return name.strip()

    def subliminal_parse(self, file_name):
        video = subliminal.Video.fromname(file_name)
        file_info = {
            'title': video.series or video.title,
            'season': video.season,
            'episode': video.episode,
            'year': video.year,
            'type': 'episode' if video.series else 'movie'
        }
        return file_info

    @lru_cache(maxsize=1024)
    def get_tmdb_id(self, title, is_movie=True):
        try:
            search = tmdb.Search()
            query = title.split("(")[0].strip()
            response = search.movie(query=query) if is_movie else search.tv(query=query)

            if response['results']:
                best_match = max(response['results'], key=lambda x: self.similarity(x['title' if is_movie else 'name'], title))
                tmdb_id = best_match['id']
                return tmdb_id

            logger.warning(f"No TMDb ID found for {'movie' if is_movie else 'series'}: {title}")
            if stop_on_error:
                sys.exit(1)
            return "N/A"
        except Exception as e:
            logger.error(f"Error fetching TMDb ID: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
            return "N/A"

    @lru_cache(maxsize=1024)
    def similarity(self, a, b):
        return Levenshtein.ratio(a, b)

    @lru_cache(maxsize=1024)
    def get_tmdb_movie_genres(self, tmdb_id):
        try:
            movie = tmdb.Movies(tmdb_id)
            response = movie.info()
            genres = response.get('genres', [])
            genre_names = [genre['name'] for genre in genres]
            return genre_names
        except Exception as e:
            logger.error(f"Error fetching movie genres from TMDb: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
            return []

    @lru_cache(maxsize=1024)
    def get_tmdb_series_title_and_year(self, tmdb_id):
        try:
            series = tmdb.TV(tmdb_id)
            response = series.info()
            title = response['name']
            year = response['first_air_date'].split('-')[0]  # Extract year from 'first_air_date'
            return title, year
        except Exception as e:
            logger.error(f"Error fetching series title and year from TMDb: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
            return None, None

    @lru_cache(maxsize=1024)
    def get_tmdb_episode_title(self, series_id, season_number, episode_number):
        try:
            season = tmdb.TV_Seasons(series_id, season_number)
            response = season.info()
            episodes = response.get('episodes', [])
            for episode in episodes:
                if episode['episode_number'] == episode_number:
                    return episode['name']
            return None
        except Exception as e:
            logger.error(f"Error fetching episode title from TMDb: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
            return None

    def process_movie(self, file_path, file_info, tmdb_id):
        title = titlecase(file_info.get('title'))
        year = file_info.get('year')

        logger.info(f"Processing movie: {file_path} with title '{title}' and year '{year}'")

        existing_symlink = self.get_symlink(file_path, is_movie=True)
        if existing_symlink and existing_symlink.is_symlink():
            logger.info(f"Symlink already exists for {file_path}: {existing_symlink}")
            return

        if not title or not year:
            logger.warning(f"Skipping file {file_path} due to missing title or year information")
            return

        genres = self.get_tmdb_movie_genres(tmdb_id)
        formatted_tmdb_id = f"tmdb-{tmdb_id}" if tmdb_id != "N/A" else ""

        if year == 2024:
            movie_dir = self.movies_target_directory / '2024' / f"{title} ({year}) {{{formatted_tmdb_id}}}"
        else:
            if genres:
                genre_dir = genres[0]  # Use the first genre for organization
                movie_dir = self.movies_target_directory / genre_dir / f"{title} ({year}) {{{formatted_tmdb_id}}}"
            else:
                movie_dir = self.movies_target_directory / 'Uncategorized' / f"{title} ({year}) {{{formatted_tmdb_id}}}"

        if movie_dir.exists() and any(movie_dir.iterdir()):
            logger.info(f"Skipping symlink creation for {file_path} as the directory {movie_dir} is not empty.")
            return

        movie_dir.mkdir(parents=True, exist_ok=True)
        
        movie_file_name = f"{title}{file_path.suffix}"
        symlink_path = movie_dir / movie_file_name

        try:
            symlink_path.symlink_to(file_path)
        except OSError as e:
            logger.error(f"Failed to create symlink for {file_path}: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
        else:
            self.add_symlink(file_path, symlink_path, is_movie=True)
            logger.info(f"Symlink created: {symlink_path}")

    def process_series(self, file_path, file_info, tmdb_id):
        series_title = titlecase(file_info.get("title") or file_info.get("series"))
        season_number = file_info.get("season")
        episode_number = file_info.get("episode")

        logger.info(f"Processing series: {file_path} with title '{series_title}', season {season_number}, episode {episode_number}")

        if file_info.get('type') == 'movie':
            logger.warning(f"Skipping file {file_path} as it appears to be a movie, not a series episode.")
            return

        if not series_title:
            logger.warning(f"Series title not found for file {file_path}, using directory name as title.")
            series_title = file_path.parent.name
            series_title = self.clean_directory_name(series_title)

        try:
            year_match = re.search(r"\((\d{4})\)", series_title)
            year = year_match.group(1) if year_match else None
        except AttributeError:
            year = None

        existing_symlink = self.get_symlink(file_path, is_movie=False)
        if existing_symlink and existing_symlink.is_symlink():
            logger.info(f"Symlink already exists for {file_path}: {existing_symlink}")
            return

        if not all([series_title, season_number, episode_number]):
            logger.warning(f"Missing information for series processing: {file_path}")
            if stop_on_error:
                sys.exit(1)
            return

        if isinstance(season_number, list):
            season_number = season_number[0]
        if isinstance(episode_number, list):
            episode_number = episode_number[0]

        episode_title = self.get_tmdb_episode_title(tmdb_id, season_number, episode_number) or "Unknown Title"

        series_file_name = f"{series_title} - s{season_number:02d}e{episode_number:02d} - {episode_title}{file_path.suffix}"

        def normalize_dir_name(name):
            year_match = re.search(r"\((\d{4})\)", name)
            year_part = year_match.group(0) if year_match else ""
            name_without_brackets = re.sub(r"[()]", "", name)
            normalized_name = name_without_brackets.title()
            return normalized_name + year_part
        
        normalized_series_dir_name = normalize_dir_name(series_title)

        existing_series_dirs = [d for d in self.series_target_directory.iterdir() if normalize_dir_name(d.name) == normalize_dir_name(series_title)]
        if existing_series_dirs:
            counter = 1
            while True:
                new_series_dir_name = f"{series_title} ({year}) ({counter})" if counter > 1 else series_title
                new_series_dir = self.series_target_directory / new_series_dir_name
                if normalize_dir_name(new_series_dir_name) not in [normalize_dir_name(d.name) for d in existing_series_dirs]:
                    break
                counter += 1

            logger.warning(f"Series directory already exists. Creating a new directory: {new_series_dir}")
            series_dir = new_series_dir
        else:
            if tmdb_id != "N/A":
                formatted_tmdb_id = f"tmdb-{tmdb_id}"
                # Fetch series title and year from TMDb
                tmdb_series_title, tmdb_series_year = self.get_tmdb_series_title_and_year(tmdb_id)
                if tmdb_series_title:
                    series_title = tmdb_series_title
                if tmdb_series_year:
                    year = tmdb_series_year
                series_dir_name = f"{series_title} ({year}) {{{formatted_tmdb_id}}}" if year else f"{series_title} {{{formatted_tmdb_id}}}"
                series_dir = self.series_target_directory / series_dir_name
            else:
                series_dir_name = f"{series_title} ({year})" if year else series_title
                series_dir = self.series_target_directory / series_dir_name

        series_dir.mkdir(parents=True, exist_ok=True)
        season_dir = series_dir / f"Season {season_number:02d}"
        season_dir.mkdir(parents=True, exist_ok=True)
        
        existing_season_files = [f for f in season_dir.iterdir() if normalize_dir_name(f.name) == normalize_dir_name(series_file_name)]
        if existing_season_files:
            logger.info(f"Skipping symlink creation for {file_path} as a similar file exists in the season directory.")
            return
        
        symlink_path = season_dir / series_file_name

        try:
            symlink_path.symlink_to(file_path)
        except OSError as e:
            logger.error(f"Failed to create symlink for {file_path}: {e}", exc_info=True)
            if stop_on_error:
                sys.exit(1)
        else:
            self.add_symlink(file_path, symlink_path, is_movie=False)
            logger.info(f"Symlink created: {symlink_path}")

def run_watcher(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory):
    event_handler = Handler(movies_watch_directory, movies_target_directory, series_watch_directory, series_target_directory)
    observer = Observer()
    observer.schedule(event_handler, str(movies_watch_directory), recursive=True)
    observer.schedule(event_handler, str(series_watch_directory), recursive=True)
    observer.start()

    try:
        logger.info("Watching for new files...")
        while True:
            sleep(1)  # Add a sleep to prevent high CPU usage
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def run_first_time_setup(config):
    console.print("[bold]Running first-time setup...[/bold]", style="cyan")

    if config:
        console.print("[bold green]Configuration already exists. Skipping user input.[/bold green]")
    else:
        tmdb_api_key = input("Enter your TMDB API Key: ")
        movies_watch_directory = input("Enter Movies Watch Directory: ")
        movies_target_directory = input("Enter Movies Target Directory: ")
        series_watch_directory = input("Enter Series Watch Directory: ")
        series_target_directory = input("Enter Series Target Directory: ")
        working_directory = input("Enter Working Directory: ")

        config = {
            "TMDB_API_KEY": tmdb_api_key,
            "LINUX_PATHS": {
                "MOVIES_WATCH_DIRECTORY": movies_watch_directory,
                "MOVIES_TARGET_DIRECTORY": movies_target_directory,
                "SERIES_WATCH_DIRECTORY": series_watch_directory,
                "SERIES_TARGET_DIRECTORY": series_target_directory,
                "WORKING_DIRECTORY": working_directory
            },
            "WINDOWS_PATHS": {
                "MOVIES_WATCH_DIRECTORY": movies_watch_directory,
                "MOVIES_TARGET_DIRECTORY": movies_target_directory,
                "SERIES_WATCH_DIRECTORY": series_watch_directory,
                "SERIES_TARGET_DIRECTORY": series_target_directory,
                "WORKING_DIRECTORY": working_directory
            }
        }

        save_config(config, config_path)
        console.print("[bold green]Configuration saved successfully.[/bold green]")

    handler = Handler(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)

    def process_files_in_directory(directory, handler):
        files = []
        for subdir, _, file_list in os.walk(directory):
            for filename in file_list:
                file_path = Path(subdir) / filename
                if file_path.is_file():
                    files.append(file_path)

        for file_path in tqdm(files, desc=f"Processing {directory}"):
            logger.info(f"Found file for processing: {file_path}")
            handler.executor.submit(handler.process, file_path)

    with ThreadPoolExecutor() as executor:
        futures = []
        futures.append(executor.submit(process_files_in_directory, MOVIES_WATCH_DIRECTORY, handler))
        futures.append(executor.submit(process_files_in_directory, SERIES_WATCH_DIRECTORY, handler))

        for future in as_completed(futures):
            future.result()

    logger.info("First-time setup completed.")

def setup_service():
    console.print("[bold]Choose the service setup:[/bold]", style="cyan")
    console.print("[1] Systemd (Linux)", style="cyan")
    console.print("[2] Windows", style="cyan")
    choice = input("Enter your choice: ")

    if choice == "1":
        setup_systemd_service()
    elif choice == "2":
        setup_windows_service()
    else:
        console.print("[bold red]Invalid choice. Please enter '1' or '2'.[/bold red]")

def setup_systemd_service():
    service_content = f"""
[Unit]
Description=File Watcher Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 {script_path} --watch
WorkingDirectory={WORKING_DIRECTORY}
StandardOutput=syslog
StandardError=syslog
Restart=always

[Install]
WantedBy=multi-user.target
"""
    
    with open('/etc/systemd/system/file_watcher.service', 'w') as f:
        f.write(service_content)

    subprocess.run(['sudo', 'systemctl', 'daemon-reload'])
    subprocess.run(['sudo', 'systemctl', 'enable', 'file_watcher.service'])
    subprocess.run(['sudo', 'systemctl', 'start', 'file_watcher.service'])
    console.print("Systemd service setup complete.", style="green")

def setup_windows_service():
    batch_script_path = Path("RD-Sym.bat").resolve()
    
    batch_script_content = f"""
@echo off
cd /d %~dp0
python {script_path} --watch
"""

    with batch_script_path.open("w") as batch_file:
        batch_file.write(batch_script_content)

    service_name = "FileWatcherService"
    service_exe_path = batch_script_path
    sc_create_command = f'sc create {service_name} binPath= "{service_exe_path}" start= auto'
    
    sc_start_command = f'sc start {service_name}'

    subprocess.run(sc_create_command, shell=True)
    subprocess.run(sc_start_command, shell=True)

    console.print("Windows service setup complete.", style="green")

def main():
    parser = argparse.ArgumentParser(description="File Watcher and Organizer")
    parser.add_argument('--watch', action='store_true', help='Run watcher mode')
    parser.add_argument('--setup', action='store_true', help='Run first-time setup')
    parser.add_argument('--service', action='store_true', help='Setup service')
    
    args = parser.parse_args()
    
    if args.watch:
        run_watcher(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
    elif args.setup:
        run_first_time_setup(config)
    elif args.service:
        setup_service()
    else:
        console.print("Welcome to the script main menu:", style="cyan")
        console.print("[1] Perform first-time setup", style="cyan")
        console.print("[2] Run watcher", style="cyan")
        console.print("[3] Setup service", style="cyan")
        console.print("[4] Exit", style="cyan")
        choice = input("Enter your choice: ")

        if choice == "1":
            run_first_time_setup(config)
        elif choice == "2":
            run_watcher(MOVIES_WATCH_DIRECTORY, MOVIES_TARGET_DIRECTORY, SERIES_WATCH_DIRECTORY, SERIES_TARGET_DIRECTORY)
        elif choice == "3":
            setup_service()
        elif choice == "4":
            console.print("Quitting Script", style="cyan")
            sys.exit(1)
        else:
            console.print("[bold red]Invalid choice. Please enter '1', '2', or '3'.[/bold red]")
            sys.exit(1)

if __name__ == '__main__':
    main()
