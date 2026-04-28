import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
import requests_cache
from wav_provider import get_wav
s = requests_cache.CachedSession(...)
# cache'deki Open-Meteo isteğinin tarihine bak

get_wav(38.4, 27.1, use_soilgrids_package=False)