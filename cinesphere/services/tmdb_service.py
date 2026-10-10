import requests
import os
from flask import current_app

LANGUAGE_NAMES = {
    'te': 'Telugu',
    'ta': 'Tamil',
    'ml': 'Malayalam',
    'kn': 'Kannada',
    'hi': 'Hindi',
    'en': 'English',
    'ja': 'Japanese',
    'ko': 'Korean',
    'es': 'Spanish',
    'fr': 'French'
}

class TMDBService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv('TMDB_API_KEY', '')
        self.base_url = 'https://api.themoviedb.org/3'
        self.image_base_url = 'https://image.tmdb.org/t/p'

    def _get(self, endpoint, params=None):
        if not params:
            params = {}
        params['api_key'] = self.api_key
        try:
            resp = requests.get(f'{self.base_url}/{endpoint}', params=params, timeout=6)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            print(f'TMDB API request error for {endpoint}: {e}')
            return None

    def get_popular_movies(self, page=1):
        data = self._get('movie/popular', {'page': page})
        return data.get('results', []) if data else []

    def get_now_playing_movies(self, page=1):
        data = self._get('movie/now_playing', {'page': page})
        return data.get('results', []) if data else []

    def get_upcoming_movies(self, page=1):
        data = self._get('movie/upcoming', {'page': page})
        return data.get('results', []) if data else []

    def get_top_rated_movies(self, page=1):
        data = self._get('movie/top_rated', {'page': page})
        return data.get('results', []) if data else []

    def get_movie_details(self, tmdb_id):
        return self._get(f'movie/{tmdb_id}', {'append_to_response': 'credits,videos'})

    def get_movie_cast(self, tmdb_id):
        data = self._get(f'movie/{tmdb_id}/credits')
        return data.get('cast', []) if data else []

    def get_actor_details(self, actor_id):
        return self._get(f'person/{actor_id}', {'append_to_response': 'movie_credits'})

    def search_movies(self, query, page=1):
        data = self._get('search/movie', {'query': query, 'page': page})
        return data.get('results', []) if data else []

    def get_genres(self):
        data = self._get('genre/movie/list')
        return data.get('genres', []) if data else []

    def format_movie_data(self, tmdb_raw):
        if not tmdb_raw:
            return None
        
        # Extract cast and crew
        credits = tmdb_raw.get('credits', {})
        cast_list = []
        for c in credits.get('cast', [])[:10]:
            cast_list.append({
                'id': c.get('id'),
                'name': c.get('name'),
                'character': c.get('character'),
                'profile_path': f"{self.image_base_url}/w185{c.get('profile_path')}" if c.get('profile_path') else None
            })
            
        crew_list = credits.get('crew', [])
        director = next((m.get('name') for m in crew_list if m.get('job') == 'Director'), 'Unknown')
        writers = [m.get('name') for m in crew_list if m.get('job') in ('Writer', 'Screenplay')][:3]
        
        # Extract trailer
        videos = tmdb_raw.get('videos', {}).get('results', [])
        trailer_url = None
        for v in videos:
            if v.get('site') == 'YouTube' and v.get('type') in ('Trailer', 'Teaser'):
                trailer_url = f"https://www.youtube.com/embed/{v.get('key')}"
                break

        poster_path = tmdb_raw.get('poster_path')
        backdrop_path = tmdb_raw.get('backdrop_path')
        
        genres = [g.get('name') for g in tmdb_raw.get('genres', [])] if isinstance(tmdb_raw.get('genres'), list) else []

        raw_lang = tmdb_raw.get('original_language', 'en').lower()
        full_lang_name = LANGUAGE_NAMES.get(raw_lang, raw_lang.upper())

        is_south_indian = raw_lang in ['te', 'ta', 'ml', 'kn']

        return {
            'tmdb_id': tmdb_raw.get('id'),
            'title': tmdb_raw.get('title'),
            'original_title': tmdb_raw.get('original_title'),
            'overview': tmdb_raw.get('overview'),
            'poster_url': f"{self.image_base_url}/w500{poster_path}" if poster_path else '/static/images/default_poster.svg',
            'backdrop_url': f"{self.image_base_url}/original{backdrop_path}" if backdrop_path else '/static/images/default_backdrop.svg',
            'release_date': tmdb_raw.get('release_date'),
            'runtime': tmdb_raw.get('runtime', 135) or 135,
            'genres': genres,
            'language_code': raw_lang,
            'language': full_lang_name,
            'is_south_indian': is_south_indian,
            'rating': round(float(tmdb_raw.get('vote_average', 7.5)), 1),
            'vote_count': tmdb_raw.get('vote_count', 0),
            'popularity': tmdb_raw.get('popularity', 0.0),
            'cast': cast_list,
            'director': director,
            'writers': writers,
            'trailer_url': trailer_url,
            'status': 'Now Showing'
        }

tmdb_service = TMDBService()
